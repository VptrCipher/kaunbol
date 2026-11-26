"""Live Call Guard: relay a live call's audio to DetectifAI's streaming API.

Browser  --(PCM16 16 kHz frames, transcript text)-->  this relay  --(wss + API key)-->  DetectifAI /v1/stream
Browser  <--(live deepfake updates, verification, scam-script hits, running risk level)--  this relay

The API key stays on the server. The browser only talks to /ws/live.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import time
from dataclasses import asdict
from urllib.parse import urlencode

from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import struct

from detectif import DetectifClient, DetectifError
import botguard
import confirm
import fingerprint
import registry
from identify import by_transcript, number_status
from scam_script import analyse
from verdict import assess

BYTES_PER_SEC = 32000  # 16 kHz * 16-bit mono
MAX_BUFFER = 55 * BYTES_PER_SEC  # verify accepts up to 60 s
_active_guards = {}


def _wav(pcm: bytes) -> bytes:
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, 16000, BYTES_PER_SEC, 2, 16) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


def _pub(m: dict | None) -> dict | None:
    return {"id": m["id"], "name": m["name"], "relation": m.get("relation", "")} if m else None

STREAM_URL = os.getenv("DETECTIF_STREAM_URL", "wss://platform.detectif.ai/v1/stream")

CLOSE_REASONS = {
    1003: "invalid_request", 1011: "server_error", 1012: "service_restarted", 1013: "too_many_streams",
    4401: "invalid_api_key", 4402: "insufficient_credits", 4403: "insufficient_scope", 4429: "too_many_streams",
}


class CallState:
    """Everything known about the call so far, turned into a running risk level."""

    def __init__(self, member: dict | None, how: str | None, members: list[dict], number: str):
        self.member, self.how, self.members, self.number = member, how, members, number
        self.deepfake: dict | None = None
        self.identity: dict | None = None
        self.transcript = ""
        self.script = analyse("").as_dict()
        self.pcm = bytearray()  # buffered call audio, for a late (REST) identity check
        self.late_verify_pending = False
        self.late_verify_started = False
        self.auto_detect = False
        self.voice_tried = False
        self.rep: dict | None = None
        self.action: str | None = None
        self.incident_id: int | None = None
        self.simulated = False
        self.guard_id = None
        self.bot = None
        self.bot_events: list[dict] = []
        self.override = None
        self.replay_id = None
        self.replay_passed = False
        self.confirmation_id = None
        self.can_step = False
        self.review_sent = False

    @property
    def claimed(self) -> bool:
        return self.member is not None

    def risk(self) -> dict:
        return assess(self.deepfake, self.identity if self.claimed else None, self.script, self.claimed,
                      number=number_status(self.number, self.member, self.members), rep=self.rep,
                      action=self.action)

    def add_audio(self, chunk: bytes) -> None:
        self.pcm += chunk
        if len(self.pcm) > MAX_BUFFER:
            del self.pcm[: len(self.pcm) - MAX_BUFFER]


async def _send(ws: WebSocket, msg: dict) -> None:
    try:
        await ws.send_text(json.dumps(msg))
    except Exception:
        pass


def matching_guard(guard_id: str, member_id: str | None = None) -> str | None:
    guard = _active_guards.get(guard_id)
    if guard and (member_id is None or (guard[1].member or {}).get("id") == member_id):
        return guard_id
    return None


async def _bot_update(ws: WebSocket, state: CallState, event: botguard.Event) -> None:
    state.bot = event
    payload = asdict(event)
    state.bot_events.append(payload)
    await _send(ws, {"type": "bot_state", "id": state.guard_id, **payload})


async def pause_guard(guard_id: str | None, reason: str) -> None:
    guard = _active_guards.get(guard_id)
    if guard:
        ws, state = guard
        event = botguard.transition(state.bot, "pause", reason, time.time())
        if event != state.bot:
            await _bot_update(ws, state, event)
        await _refresh_bot_gate(ws, state)


def link_bot_check(guard_id: str | None, *, replay_id: str | None = None,
                   confirmation_id: str | None = None) -> None:
    if guard_id not in _active_guards:
        return
    _, state = _active_guards[guard_id]
    if replay_id:
        state.replay_id, state.replay_passed = replay_id, False
    if confirmation_id:
        state.confirmation_id = confirmation_id


async def refresh_guard(guard_id: str | None, passed_replay_id: str | None = None) -> None:
    if guard_id in _active_guards:
        ws, state = _active_guards[guard_id]
        if passed_replay_id and passed_replay_id == state.replay_id:
            state.replay_passed = True
        await _refresh_bot_gate(ws, state)


async def _refresh_bot_gate(ws: WebSocket, state: CallState) -> bool:
    request = confirm.by_id(state.confirmation_id) if state.confirmation_id else None
    result = (request.get("result") or {}) if request else {}
    risk = state.risk()
    ready = bool(state.bot.state == "running" and not state.review_sent and state.replay_passed
                 and state.member and not registry.device_reported(state.member["id"])
                 and request and request["status"] == "confirmed"
                 and result.get("voice") == "real" and result.get("decision") == "match"
                 and "voice_real" in risk["reasons"]
                 and (state.deepfake or {}).get("verdict") == "real"
                 and (state.identity or {}).get("decision") == "match" and risk["level"] != "high")
    if ready != state.can_step:
        state.can_step = ready
        await _send(ws, {"type": "bot_gate", "can_step": ready})
    return ready


def _risk_signature(verdict: dict) -> frozenset[str] | None:
    return frozenset(verdict["reasons"]) if verdict["level"] == "high" else None


async def _bot_control(ws: WebSocket, state: CallState, action: str) -> None:
    if action == "step":
        if await _refresh_bot_gate(ws, state):
            state.review_sent = True
            await _send(ws, {"type": "bot_line", "line": "review"})
            await _refresh_bot_gate(ws, state)
        else:
            await _send(ws, {"type": "bot_gate", "can_step": False})
        return
    if action not in ("handover", "resume"):
        return
    event = botguard.transition(state.bot, action, "", time.time())
    if event != state.bot:
        if action == "resume":
            state.override = _risk_signature(state.risk())
        await _bot_update(ws, state, event)
        await _refresh_bot_gate(ws, state)


async def _on_upstream(ws: WebSocket, state: CallState, msg: dict, simulated: bool) -> bool:
    """Handle one message from DetectifAI. Returns True when the stream is finished."""
    t = msg.get("type")
    msg["simulated"] = simulated
    state.simulated = state.simulated or simulated
    if t == "deepfake":
        state.deepfake = {"verdict": msg.get("verdict"), "score": msg.get("score"), "segments": []}
    elif t == "verification":
        state.identity = msg.get("result")
    elif t == "completed":
        res = msg.get("results") or {}
        df = res.get("deepfake") or {}
        if df.get("result"):
            state.deepfake = df["result"]
        elif df.get("error") and state.deepfake is None:
            state.deepfake = {"error": df["error"]}
        ver = res.get("verification") or {}
        if ver.get("result"):
            state.identity = ver["result"]
    await _send(ws, msg)
    if t in ("deepfake", "verification", "completed"):
        await _risk(ws, state, final=t == "completed")
    return t == "completed"


async def _risk(ws: WebSocket, state: CallState, final: bool = False) -> None:
    msg = {"type": "risk", "final": final, "verdict": state.risk(), "deepfake": state.deepfake,
           "identity": state.identity, "script": state.script, "claimed": _pub(state.member),
           "identified_by": state.how, "number_info": state.rep, "simulated": state.simulated}
    if state.bot:
        reason = botguard.pause_reason(msg["verdict"])
        if reason is None:
            state.override = None
        elif state.override != _risk_signature(msg["verdict"]):
            await pause_guard(state.guard_id, reason)
        msg["bot_events"] = state.bot_events
        await _refresh_bot_gate(ws, state)
    if final and state.incident_id is None:
        state.incident_id = registry.record("live", state.number, msg, state.transcript)
        msg["incident_id"] = state.incident_id
    await _send(ws, msg)


async def _late_verify(ws: WebSocket, state: CallState, client: DetectifClient, scenario: str) -> None:
    """The caller named themselves mid-call: 1:1 check of the audio so far against that member."""
    state.late_verify_started = True
    try:
        res = await client.verify(_wav(bytes(state.pcm)), "call.wav", state.member["speaker_id"], scenario,
                                  detect_deepfake=False)  # the stream already checks liveness
        state.identity = res.get("result")
        if state.how == "voice" and (state.identity or {}).get("decision") == "no_match":
            # Only a guess: no match means "not a saved family voice", not "impostor".
            await _send(ws, {"type": "not_family", "closest": state.member["name"],
                             "similarity": state.identity.get("similarity")})
            state.member, state.identity, state.how = None, None, None
        else:
            await _send(ws, {"type": "verification", "final": True, "result": state.identity, "late": True,
                             "simulated": bool(res.get("simulated"))})
    except DetectifError as e:
        await _send(ws, {"type": "verification_error", "code": e.code})
    await _risk(ws, state)


async def _maybe_late_verify(ws: WebSocket, state: CallState, client: DetectifClient, scenario: str) -> None:
    # Nobody claimed yet: after ~8 s of audio, shortlist the closest family voice for free.
    if (state.member is None and state.auto_detect and not state.voice_tried
            and len(state.pcm) >= 8 * BYTES_PER_SEC):
        state.voice_tried = True
        ranked = fingerprint.rank(fingerprint.embed(_wav(bytes(state.pcm))) or [], state.members) \
            if state.members else []
        if ranked:
            best, score = ranked[0]
            state.member, state.how, state.late_verify_pending = best, "voice", True
            await _send(ws, {"type": "identified", "member": _pub(best), "by": "voice", "score": round(score, 3)})
    if state.late_verify_pending and not state.late_verify_started and len(state.pcm) >= 6 * BYTES_PER_SEC:
        state.late_verify_started = True
        asyncio.create_task(_late_verify(ws, state, client, scenario))


async def _on_client_text(ws: WebSocket, state: CallState, text: str, client=None, scenario: str = "") -> str | None:
    """Handle a JSON message from the browser. Returns 'stop' when the user ends the call."""
    try:
        m = json.loads(text)
    except ValueError:
        return None
    if not isinstance(m, dict):
        return None
    if m.get("type") == "bot_control" and state.bot:
        await _bot_control(ws, state, m.get("action"))
    elif m.get("type") == "transcript":
        state.transcript = str(m.get("text", ""))[:5000]
        before = state.script.get("level")
        state.script = analyse(state.transcript).as_dict()
        await _send(ws, {"type": "script", **state.script})
        if state.member is None and client is not None:
            found = by_transcript(state.transcript, state.members)
            if found:
                state.member, state.how, state.late_verify_pending = found, "transcript", True
                await _send(ws, {"type": "identified", "member": _pub(found), "by": "transcript"})
                await _maybe_late_verify(ws, state, client, scenario)
                before = None  # force a risk refresh
        if state.script.get("level") != before:
            await _risk(ws, state)
    elif m.get("type") == "stop":
        return "stop"
    return None


async def run_live(ws: WebSocket, client: DetectifClient, member: dict | None, how: str | None,
                   members: list[dict], number: str, scenario: str, auto_detect: bool = True,
                   rep: dict | None = None, action: str = "", bot: bool = False) -> None:
    await ws.accept(subprotocol=ws.scope.get("kb_subprotocol"))  # echo the PIN token or the browser drops the socket
    state = CallState(member, how, members, number)
    state.auto_detect = auto_detect
    state.rep = rep
    state.action = action or None
    await _send(ws, {"type": "number_info", **(rep or {})})
    if member:
        await _send(ws, {"type": "identified", "member": _pub(member), "by": how})
    state.simulated = client.demo
    if bot:
        state.guard_id = secrets.token_urlsafe(16)
        _active_guards[state.guard_id] = (ws, state)
        reason = botguard.pause_reason(state.risk())
        await _bot_update(ws, state, botguard.Event("paused" if reason else "running", reason or "started", time.time()))
    try:
        if client.demo:
            await _run_simulated(ws, state, member, scenario, client)
        else:
            await _run_provider(ws, state, client, scenario)
    finally:
        if bot:
            try:
                if state.incident_id is None:
                    await _risk(ws, state, final=True)
            finally:
                _active_guards.pop(state.guard_id, None)


async def _run_provider(ws: WebSocket, state: CallState, client: DetectifClient, scenario: str) -> None:
    params = {"checks": "deepfake,verify" if state.member else "deepfake", "encoding": "pcm_s16le",
              "sample_rate": "16000", "update_seconds": "5", "reference": "kaunbol-live"}
    if state.member:
        params["speaker_id"] = state.member["speaker_id"]
    url = f"{STREAM_URL}?{urlencode(params)}"
    try:
        upstream = await connect(url, additional_headers={"Authorization": f"Bearer {client.api_key}"},
                                 max_size=2**22, open_timeout=15)
    except Exception as e:  # handshake refused (bad key, no credits, ...)
        await _send(ws, {"type": "error", "code": "stream_unavailable", "message": str(e)[:200]})
        await ws.close()
        return

    async def browser_to_upstream():
        try:
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    break
                if m.get("bytes"):
                    await upstream.send(m["bytes"])
                    state.add_audio(m["bytes"])
                    await _maybe_late_verify(ws, state, client, scenario)
                elif m.get("text") and await _on_client_text(ws, state, m["text"], client, scenario) == "stop":
                    await upstream.send(json.dumps({"type": "stop"}))
        except (WebSocketDisconnect, ConnectionClosed, RuntimeError):
            pass
        finally:
            try:
                await upstream.send(json.dumps({"type": "stop"}))
            except Exception:
                pass

    async def upstream_to_browser():
        try:
            async for raw in upstream:
                if isinstance(raw, bytes):
                    continue
                if await _on_upstream(ws, state, json.loads(raw), simulated=False):
                    break
        except ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else None
            if code and code != 1000:
                await _send(ws, {"type": "error", "code": CLOSE_REASONS.get(code, f"closed_{code}"),
                                 "message": (e.rcvd.reason if e.rcvd else "")[:200]})

    up = asyncio.create_task(upstream_to_browser())
    down = asyncio.create_task(browser_to_upstream())
    await up  # the stream is over when DetectifAI says "completed" or closes
    down.cancel()
    await upstream.close()
    try:
        await ws.close()
    except Exception:
        pass


# --------------------------------------------------------------- demo stream
async def _run_simulated(ws: WebSocket, state: CallState, member: dict | None, scenario: str,
                         client: DetectifClient) -> None:
    """Same message shapes as the real stream, clearly marked simulated."""
    fake = scenario == "cloned"
    wrong = scenario == "wrong_person"
    received = {"bytes": 0}
    stopped = asyncio.Event()

    async def reader():
        try:
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    break
                if m.get("bytes"):
                    received["bytes"] += len(m["bytes"])
                    state.add_audio(m["bytes"])
                    await _maybe_late_verify(ws, state, client, scenario)
                elif m.get("text") and await _on_client_text(ws, state, m["text"], client, scenario) == "stop":
                    break
        except Exception:
            pass
        stopped.set()

    task = asyncio.create_task(reader())
    await _send(ws, {"type": "ready", "stream_id": "str_demo", "checks": ["deepfake", "verify"] if member else ["deepfake"],
                     "reference": "kaunbol-live", "simulated": True})
    seq, sent_verify = 0, False
    segments: list[dict] = []
    while not stopped.is_set():
        try:
            await asyncio.wait_for(stopped.wait(), timeout=2.5)
        except asyncio.TimeoutError:
            pass
        secs = received["bytes"] / 32000
        if secs < 4 or stopped.is_set():
            continue
        seq += 1
        score = round((0.84 + random.random() * 0.12) if fake else (0.02 + random.random() * 0.08), 4)
        segments.append({"start": round(max(secs - 4, 0), 1), "end": round(secs, 1), "score": score,
                         "verdict": "fake" if score >= 0.5 else "real"})
        await _on_upstream(ws, state, {"type": "deepfake", "early": seq == 1, "final": False, "sequence": seq,
                                       "seconds": round(secs, 1), "speech_seconds": round(secs * 0.85, 1),
                                       "verdict": "fake" if fake else "real", "score": score}, simulated=True)
        if member and not sent_verify and secs >= 6:
            sent_verify = True
            sim = round(0.1 + random.random() * 0.2, 4) if wrong else round(0.7 + random.random() * 0.15, 4)
            await _on_upstream(ws, state, {"type": "verification", "final": True, "speaker_id": member["speaker_id"],
                                           "result": {"decision": "no_match" if wrong else "match", "similarity": sim}},
                               simulated=True)
    secs = received["bytes"] / 32000
    final = {"verdict": "fake" if fake else "real",
             "score": round(sum(s["score"] for s in segments) / len(segments), 4) if segments else 0,
             "duration_seconds": round(secs, 1), "segments": segments}
    results = {"deepfake": {"result": final} if segments else {"error": {"code": "insufficient_speech"}}}
    if state.identity:
        results["verification"] = {"result": state.identity}
    await _on_upstream(ws, state, {"type": "completed", "status": "completed", "seconds": round(secs, 1),
                                   "ended_by": "client_stop", "results": results}, simulated=True)
    task.cancel()
    try:
        await ws.close()
    except Exception:
        pass
