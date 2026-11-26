"""Kaun Bol Raha Hai? — family voice-scam shield.

Run:  python -m uvicorn app:app --reload --port 8000   (from backend/)
"""
from __future__ import annotations

import hashlib
import asyncio
import hmac
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx
import qrcode
from qrcode.image.svg import SvgPathImage
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import MutableHeaders, URL
from starlette.exceptions import HTTPException as StarletteHTTPException

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")

from detectif import DetectifClient, DetectifError  # noqa: E402
import challenge  # noqa: E402
import benchmark  # noqa: E402
import confirm  # noqa: E402
import outbound  # noqa: E402
import fingerprint  # noqa: E402
import registry  # noqa: E402
import report  # noqa: E402
import stepup  # noqa: E402
import transactions  # noqa: E402
from identify import norm_phone, number_status, resolve  # noqa: E402
from live import link_bot_check, matching_guard, pause_guard, refresh_guard, run_live  # noqa: E402
from scam_script import analyse  # noqa: E402
from verdict import assess  # noqa: E402

DATA = Path(os.getenv("KAUNBOL_DATA", ROOT.parent / "data"))
DATA.mkdir(parents=True, exist_ok=True)
STORE = DATA / "family.json"
FRONTEND = ROOT.parent / "frontend"
MAX_UPLOAD = 25 * 1024 * 1024

client = DetectifClient(
    api_key=os.getenv("DETECTIF_API_KEY") or None,
    demo=os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes"),
)
@asynccontextmanager
async def lifespan(_app):
    logging.getLogger("uvicorn.error").warning(
        "Single worker only: active call, confirmation and replay sessions are held in memory."
    )
    yield


app = FastAPI(title="Kaun Bol Raha Hai?", lifespan=lifespan)
_confirmation_origin = urlsplit(stepup.confirmation_base())
CONFIRM_HOST = _confirmation_origin.hostname if _confirmation_origin.scheme == "https" else None
DEVICE_PAGE = re.compile(r"/confirm/[A-Za-z0-9_-]+")
DEVICE_API = re.compile(r"/api/confirm/device/[A-Za-z0-9_-]+(?:/(?:approve|deny|language|otp-start|otp-check))?")
DEVICE_ASSETS = {"/static/styles.css", "/static/i18n.js", "/static/audio.js"}


class CustomerSite:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        page_or_api = bool(DEVICE_PAGE.fullmatch(path) or DEVICE_API.fullmatch(path))
        if CONFIRM_HOST and URL(scope=scope).hostname == CONFIRM_HOST:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4404})
            if not (page_or_api or path in DEVICE_ASSETS):
                return await Response(status_code=404)(scope, receive, send)
        if not page_or_api:
            return await self.app(scope, receive, send)

        async def private_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        return await self.app(scope, receive, private_headers)


app.add_middleware(CustomerSite)
registry.init(DATA / ("kaunbol-demo.db" if client.demo else "kaunbol.db"))  # simulated results never flag real numbers
_lock = threading.Lock()


# ---------------------------------------------------------------- local store
def _load() -> dict:
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {"members": [], "safeword": None, "history": []}


def _save(d: dict) -> None:
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE)


def _public_member(m: dict) -> dict:
    out = {k: m[k] for k in ("id", "name", "relation", "samples", "created_at")}
    out["phones"] = m.get("phones", [])
    out["agent"] = bool(m.get("agent"))
    out["email"] = m.get("email", "")
    return out


def _norm_phrase(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.casefold())


def _hash_phrase(phrase: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", _norm_phrase(phrase).encode(), bytes.fromhex(salt), 200_000).hex()


async def _read(upload: UploadFile) -> tuple[bytes, str]:
    data = await upload.read()
    if not data:
        raise HTTPException(400, detail={"code": "missing_file"})
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, detail={"code": "file_too_large"})
    return data, upload.filename or "clip.wav"


def _api_error(e: DetectifError) -> JSONResponse:
    code = "invalid_api_key" if e.status == 401 else e.code
    return JSONResponse(status_code=e.status if e.status < 500 else 502,
                        content={"detail": {"code": code, "message": e.message}})


# ------------------------------------------------------------ agent access
# Agent-side routes (checks, call log, enrolment, outbound calls) need the shared agent PIN when
# AGENT_PIN is set. Customer-side routes (/verify, /confirm/<token>) stay open: they are scoped
# by single-use tokens or only reveal "is my bank calling me". A real deployment would replace
# this with the bank's SSO; the PIN keeps a public demo URL from exposing the agent console.
#
# The PIN travels in the `X-Agent-Pin` header only (never in a URL, where it would reach server logs,
# proxy logs and browser history). Browsers cannot set headers on a WebSocket, so the socket carries it
# in the Sec-WebSocket-Protocol header as "kb.<hex of the PIN>". Downloads (report, CSV) are fetched with
# the header and opened as blobs. Wrong guesses are rate-limited: 5 per minute per client, 50 overall.
_FAIL_MAX, _FAIL_WINDOW, _FAIL_GLOBAL = 5, 60.0, 50
_fails: dict[str, list[float]] = {}
_fail_lock = threading.Lock()


def _client_key(conn) -> str:
    """Who is guessing. Behind a tunnel or proxy every request comes from the proxy, so only trust the
    forwarded address when TRUST_PROXY=1 (a client can fake that header if you are NOT behind a proxy)."""
    if os.getenv("TRUST_PROXY", "").lower() in ("1", "true", "yes"):
        fwd = conn.headers.get("cf-connecting-ip") or (conn.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if fwd:
            return fwd
    return conn.client.host if conn.client else "?"


def _recent(key: str, now: float) -> list[float]:
    hits = [t for t in _fails.get(key, []) if now - t < _FAIL_WINDOW]
    _fails[key] = hits
    return hits


def _pin_state(conn, given: str) -> str:
    """'ok' | 'bad' | 'locked'. With no AGENT_PIN configured everything is 'ok'."""
    pin = os.getenv("AGENT_PIN", "")
    if not pin:
        return "ok"
    key, now = _client_key(conn), time.time()
    with _fail_lock:
        if len(_recent(key, now)) >= _FAIL_MAX or len(_recent("*", now)) >= _FAIL_GLOBAL:
            return "locked"
        if hmac.compare_digest(given.encode(), pin.encode()):
            _fails.pop(key, None)
            return "ok"
        if not given:  # no PIN sent yet (the app fires several calls before it asks): refuse, but it is not a guess
            return "bad"
        _fails[key].append(now)
        _fails["*"].append(now)
        return "bad"


def _pin_from_subprotocol(ws: WebSocket) -> tuple[str, str | None]:
    """Returns (pin, the protocol token to echo back). Token format: kb.<hex of the PIN's UTF-8 bytes>."""
    for tok in (ws.headers.get("sec-websocket-protocol") or "").split(","):
        tok = tok.strip()
        if tok.startswith("kb."):
            try:
                return bytes.fromhex(tok[3:]).decode("utf-8"), tok
            except ValueError:
                return "", tok
    return "", None


def agent_only(request: Request) -> None:
    state = _pin_state(request, request.headers.get("x-agent-pin") or "")
    if state == "locked":
        raise HTTPException(429, detail={"code": "too_many_attempts"}, headers={"Retry-After": str(int(_FAIL_WINDOW))})
    if state != "ok":
        raise HTTPException(401, detail={"code": "invalid_pin"})


_customer_requests: dict[str, deque[float]] = defaultdict(deque)
_customer_lock = threading.Lock()
_customer_sweep = 0.0


def customer_limit(request: Request) -> None:
    """Share a 30-request rolling window across public customer APIs."""
    global _customer_sweep
    now = monotonic()
    key = _client_key(request)
    with _customer_lock:
        if now >= _customer_sweep:
            for old in [k for k, hits in _customer_requests.items() if not hits or hits[-1] <= now - 60]:
                del _customer_requests[old]
            _customer_sweep = now + 60
        hits = _customer_requests[key]
        while hits and hits[0] <= now - 60:
            hits.popleft()
        if len(hits) >= 30:
            retry = max(1, int(hits[0] + 60 - now) + 1)
            raise HTTPException(429, detail={"code": "too_many_attempts"}, headers={"Retry-After": str(retry)})
        hits.append(now)


# ------------------------------------------------------------------- routes
@app.get("/api/status")
async def status():
    d = _load()
    return {
        "pin_required": bool(os.getenv("AGENT_PIN")),
        "mode": "demo" if client.demo else "live",
        "members": len(d["members"]),
        "safeword_set": bool(d.get("safeword")),
        "sample_clips": [name for name in SAMPLE_NAMES if _clip_path(name)],
        "credits": await client.credits(),
    }


def _visible(m: dict) -> bool:
    # Voices "enrolled" in demo mode never reached DetectifAI, so live mode must not offer them.
    return client.demo or not m.get("simulated")


@app.get("/api/family", dependencies=[Depends(agent_only)])
def family():
    return [_public_member(m) for m in _load()["members"] if _visible(m)]


@app.post("/api/family/enroll", dependencies=[Depends(agent_only)])
async def enroll(
    file: UploadFile = File(...),
    name: str = Form(..., max_length=60),
    relation: str = Form("", max_length=40),
    member_id: str = Form(""),
    phone: str = Form("", max_length=20),
):
    """Enroll a new family member, or add another sample (max 5) to an existing one."""
    audio, fname = await _read(file)
    with _lock:
        d = _load()
        member = next((m for m in d["members"] if m["id"] == member_id), None) if member_id else None
    if member is None:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "member"
        speaker_id = f"kb-{slug}-{secrets.token_hex(3)}"
    else:
        speaker_id = member["speaker_id"]
    try:
        res = await client.enroll(audio, fname, speaker_id, name.strip())
    except DetectifError as e:
        return _api_error(e)
    samples = res.get("speaker", {}).get("samples", 1)
    fp = fingerprint.embed(audio)
    with _lock:
        d = _load()
        existing = next((m for m in d["members"] if m["speaker_id"] == speaker_id), None)
        if existing:
            existing["samples"] = samples
            m = existing
            if norm_phone(phone) and norm_phone(phone) not in map(norm_phone, m.setdefault("phones", [])):
                m["phones"].append(phone.strip())
            if fp:
                m.setdefault("prints", []).append(fp)
        else:
            m = {"id": secrets.token_hex(8), "name": name.strip(), "relation": relation.strip(),
                 "speaker_id": speaker_id, "samples": samples, "created_at": int(time.time()),
                 "simulated": bool(res.get("simulated")),
                 "phones": [phone.strip()] if norm_phone(phone) else [],
                 "prints": [fp] if fp else []}
            d["members"].append(m)
        _save(d)
    return {"member": _public_member(m), "quality": res.get("quality"),
            "simulated": res.get("simulated", False)}


class Phones(BaseModel):
    phones: list[str]


class CustomerEmail(BaseModel):
    email: str = Field(max_length=254)


@app.put("/api/family/{member_id}/email", dependencies=[Depends(agent_only)])
def set_customer_email(member_id: str, body: CustomerEmail):
    address = stepup.email_address(body.email)
    if body.email.strip() and not address:
        raise HTTPException(422, detail={"code": "email_invalid"})
    with _lock:
        d = _load()
        member = next((m for m in d["members"] if m["id"] == member_id and _visible(m)), None)
        if not member:
            raise HTTPException(404, detail={"code": "member_not_found"})
        member["email"] = address
        _save(d)
    return _public_member(member)


@app.put("/api/family/{member_id}/phones", dependencies=[Depends(agent_only)])
def set_phones(member_id: str, body: Phones):
    """Saved numbers work like a bank's caller ID: they pick whose voice to compare against."""
    with _lock:
        d = _load()
        m = next((x for x in d["members"] if x["id"] == member_id), None)
        if not m:
            raise HTTPException(404, detail={"code": "member_not_found"})
        m["phones"] = [p.strip()[:20] for p in body.phones if len(norm_phone(p)) >= 6][:5]
        _save(d)
    return _public_member(m)


@app.delete("/api/family/{member_id}", dependencies=[Depends(agent_only)])
async def remove_member(member_id: str):
    with _lock:
        d = _load()
        m = next((x for x in d["members"] if x["id"] == member_id), None)
    if not m:
        raise HTTPException(404, detail={"code": "member_not_found"})
    try:
        await client.delete_speaker(m["speaker_id"])  # also deletes the voice samples at DetectifAI
    except DetectifError as e:
        return _api_error(e)
    with _lock:
        d = _load()
        d["members"] = [x for x in d["members"] if x["id"] != member_id]
        _save(d)
    return {"deleted": True}


@app.post("/api/check", dependencies=[Depends(agent_only)])
async def check(
    file: UploadFile = File(...),
    claimed_member: str = Form(""),
    transcript: str = Form("", max_length=5000),
    demo_scenario: str = Form("genuine"),
    caller_number: str = Form("", max_length=20),
    auto_detect: bool = Form(True),
    number_hidden: bool = Form(False),
    action: str = Form("", max_length=30),
):
    if not norm_phone(caller_number) and not number_hidden:
        raise HTTPException(400, detail={"code": "number_required"})
    audio, fname = await _read(file)
    d = _load()
    members = [m for m in d["members"] if _visible(m)]
    # Like a bank: find the ONE person the caller claims to be, then a 1:1 voice check.
    member, identified_by = resolve(claimed_member, caller_number, transcript, members)
    voice_guess = None
    if member is None and auto_detect:
        member, voice_guess = _guess_by_voice(audio, members)
        if member:
            identified_by = "voice"
    number = number_status(caller_number, member, members)
    rep = registry.lookup(caller_number, members, hidden=number_hidden)
    script = analyse(transcript).as_dict()

    try:
        deepfake, identity, operation_id, simulated, note = await _voice_checks(audio, fname, member, demo_scenario)
    except DetectifError as e:
        return _api_error(e)
    not_family = None
    if identified_by == "voice" and (identity or {}).get("decision") == "no_match":
        # We only guessed who it was; a no-match means "not a saved family voice", not "impostor".
        not_family = {"closest": member["name"], "similarity": identity.get("similarity")}
        member, identity, identified_by = None, None, None
        number = number_status(caller_number, None, members)  # compare the number without the rejected guess
    out = _finish_check(d, member, identified_by, number, script, deepfake, identity, operation_id, simulated, note, rep,
                        action)
    out["voice_guess"] = voice_guess
    out["not_family"] = not_family
    out["number_info"] = rep
    out["incident_id"] = registry.record("check", "" if number_hidden else caller_number, out, transcript)
    return out


def _guess_by_voice(audio: bytes, members: list[dict]):
    """Free local shortlist: the family voice this clip sounds closest to (confirmed by Eidos next)."""
    probe = fingerprint.embed(audio)
    if not probe:
        return None, None
    ranked = fingerprint.rank(probe, members)
    if not ranked:
        return None, None
    best, score = ranked[0]
    return best, {"name": best["name"], "score": round(score, 3),
                  "ranking": [{"name": m["name"], "score": round(sc, 3)} for m, sc in ranked]}


async def _voice_checks(audio: bytes, fname: str, member: dict | None, demo_scenario: str):
    """Eidos verify (+ deepfake in the same call) when we know who they claim to be, else Aletheia alone."""
    deepfake = identity = None
    operation_id = None
    simulated = False
    note = None
    if member:
        # One Eidos call does both checks: right person + real voice.
        try:
            res = await client.verify(audio, fname, member["speaker_id"], demo_scenario)
            identity = res.get("result")
            deepfake = res.get("deepfake")
            operation_id = (deepfake or {}).get("operation_id") or res.get("operation_id")
            simulated = res.get("simulated", False)
        except DetectifError as e:
            if e.code not in ("audio_too_long", "audio_too_short", "insufficient_speech", "speaker_not_found"):
                raise
            note = e.code  # identity could not be checked; still run the liveness check
    if deepfake is None or "error" in (deepfake or {}):
        res = await client.detect(audio, fname, demo_scenario)
        deepfake = res.get("result")
        operation_id = res.get("operation_id")
        simulated = res.get("simulated", False)
    return deepfake, identity, operation_id, simulated, note


def _finish_check(d, member, identified_by, number, script, deepfake, identity, operation_id, simulated, note, rep=None,
                  action=None):
    verdict = assess(deepfake, identity if member else None, script, claimed=bool(member), number=number, rep=rep,
                     action=action or None)

    out = {
        "verdict": verdict,
        "deepfake": deepfake,
        "identity": identity,
        "script": script,
        "claimed": _public_member(member) if member else None,
        "operation_id": operation_id,
        "simulated": simulated,
        "note": note,
        "identified_by": identified_by,
        "number_status": number,
        "checked_at": int(time.time()),
    }
    with _lock:
        d = _load()
        d.setdefault("history", []).insert(0, {
            "at": out["checked_at"], "level": verdict["level"],
            "claimed": member["name"] if member else None, "simulated": simulated,
        })
        d["history"] = d["history"][:50]
        _save(d)
    return out


class ChallengeReq(BaseModel):
    lang: str = "en"
    guard_id: str = ""


@app.post("/api/challenge", dependencies=[Depends(agent_only)])
async def new_challenge(req: ChallengeReq):
    """Replay check: a fresh random phrase the caller must say back."""
    guard = matching_guard(req.guard_id)
    issued = challenge.issue(req.lang if req.lang in challenge.WORDS else "en", guard)
    link_bot_check(guard, replay_id=issued["id"])
    await refresh_guard(guard)
    return issued


@app.post("/api/challenge/{cid}/answer", dependencies=[Depends(agent_only)])
async def challenge_answer(
    cid: str,
    transcript: str = Form("", max_length=2000),
    file: UploadFile | None = File(None),
    claimed_member: str = Form(""),
    demo_scenario: str = Form("genuine"),
    final: bool = Form(False),
    incident_id: int = Form(0),
):
    """With a file: phrase + voice checks on the answer (single use).
    Without a file (live call, the stream is already checking the voice): phrase only."""
    final = final or file is not None
    phrase = challenge.check(cid, transcript, consume=final)
    out: dict = {"phrase": phrase}
    if file is not None:
        audio, fname = await _read(file)
        members = [m for m in _load()["members"] if _visible(m)]
        member = next((m for m in members if m["id"] == claimed_member), None) if claimed_member else None
        try:
            deepfake, identity, operation_id, simulated, note = await _voice_checks(audio, fname, member, demo_scenario)
        except DetectifError as e:
            return _api_error(e)
        out.update(deepfake=deepfake, identity=identity, simulated=simulated, operation_id=operation_id)
    df_fake = (out.get("deepfake") or {}).get("verdict") == "fake"
    no_match = (out.get("identity") or {}).get("decision") == "no_match"
    if phrase["status"] in ("unknown", "used", "expired"):
        out["result"] = phrase["status"]
    elif df_fake:
        out["result"] = "failed_ai"
    elif no_match:
        out["result"] = "failed_person"
    elif phrase["status"] == "passed":
        out["result"] = "passed"
    else:
        out["result"] = "failed_phrase" if final else "waiting"
    if incident_id and out["result"] != "waiting":
        registry.set_replay(incident_id, out["result"])
    if out["result"] in ("failed_phrase", "failed_person", "failed_ai", "expired"):
        await pause_guard(challenge.take_guard(cid), "replay_failed")
    elif out["result"] == "passed":
        await refresh_guard(challenge.take_guard(cid), passed_replay_id=cid)
    return out


class Feedback(BaseModel):
    operation_id: str
    correct: bool
    label: str | None = None


@app.post("/api/feedback", dependencies=[Depends(agent_only)])
async def feedback(fb: Feedback):
    if not re.fullmatch(r"op_[A-Za-z0-9_]+", fb.operation_id):
        raise HTTPException(400, detail={"code": "invalid_operation"})
    try:
        return await client.feedback(fb.operation_id, fb.correct, fb.label)
    except DetectifError as e:
        return _api_error(e)


class SafeWord(BaseModel):
    phrase: str


@app.post("/api/safeword", dependencies=[Depends(agent_only)])
def set_safeword(sw: SafeWord):
    if len(_norm_phrase(sw.phrase)) < 4:
        raise HTTPException(400, detail={"code": "safeword_too_short"})
    salt = secrets.token_hex(16)
    with _lock:
        d = _load()
        d["safeword"] = {"salt": salt, "hash": _hash_phrase(sw.phrase, salt), "fails": 0}
        _save(d)
    return {"set": True}


@app.post("/api/safeword/check", dependencies=[Depends(agent_only)])
def check_safeword(sw: SafeWord):
    with _lock:
        d = _load()
        s = d.get("safeword")
        if not s:
            raise HTTPException(400, detail={"code": "safeword_not_set"})
        ok = hmac.compare_digest(_hash_phrase(sw.phrase, s["salt"]), s["hash"])
        s["fails"] = 0 if ok else s.get("fails", 0) + 1
        _save(d)
    return {"match": ok, "fails": s["fails"]}


@app.websocket("/ws/live")
async def live_call(ws: WebSocket, member: str = "", scenario: str = "genuine", number: str = "", auto: int = 1,
                    hidden: int = 0, action: str = "", bot: bool = False):
    given, proto = _pin_from_subprotocol(ws)
    if _pin_state(ws, given) != "ok":  # wrong PIN or locked out: refuse the handshake
        await ws.close(code=4401)
        return
    ws.scope["kb_subprotocol"] = proto
    members = [x for x in _load()["members"] if _visible(x)]
    m, how = resolve(member, number, "", members)
    rep = registry.lookup(number, members, hidden=bool(hidden))
    await run_live(ws, client, m, how, members, "" if hidden else number, scenario, auto_detect=bool(auto), rep=rep,
                   action=action[:30], bot=bot)


@app.get("/api/history", dependencies=[Depends(agent_only)])
def history():
    return [{"at": r["at"], "level": r["level"], "claimed": r["claimed"], "simulated": bool(r["simulated"]),
             "number": r["display"], "id": r["id"], "kind": r["kind"],
             "bot_events": json.loads(r["bot_events"])} for r in registry.incidents(50)]


# ------------------------------------------------------------- incoming calls
# A phone system (Twilio / Exotel / Knowlarity / an Android call-screening app) calls this
# the moment a call starts, with the caller's number. The app picks it up automatically,
# so nobody types the number. Withheld numbers are marked hidden automatically.
INCOMING: list[dict] = []
INCOMING_TOKEN = os.getenv("INCOMING_TOKEN", "")  # set it to require ?token=... from the phone system
HIDDEN_WORDS = {"", "anonymous", "restricted", "unknown", "private", "withheld", "unavailable"}


@app.post("/api/calls/incoming")
async def incoming_call(request: Request):
    """Accepts JSON {"from": ..., "call_id": ...} or a Twilio-style form (From, CallSid, To)."""
    if INCOMING_TOKEN and request.query_params.get("token") != INCOMING_TOKEN:
        raise HTTPException(401, detail={"code": "invalid_token"})
    ctype = request.headers.get("content-type", "")
    data = await request.json() if "json" in ctype else dict(await request.form())
    raw = str(data.get("from") or data.get("From") or data.get("caller") or "").strip()
    hidden = raw.lower().lstrip("+") in HIDDEN_WORDS or len(norm_phone(raw)) < 6
    members = [m for m in _load()["members"] if _visible(m)]
    member, _ = resolve("", "" if hidden else raw, "", members)
    call = {
        "id": (INCOMING[-1]["id"] + 1) if INCOMING else 1,
        "at": int(time.time()),
        "number": "" if hidden else raw,
        "hidden": hidden,
        "call_id": str(data.get("call_id") or data.get("CallSid") or "")[:64],
        "info": registry.lookup(raw, members, hidden=hidden),
        "member": _public_member(member) if member else None,
        "source": str(data.get("source") or ("twilio" if "CallSid" in data else "api"))[:20],
    }
    INCOMING.append(call)
    del INCOMING[:-50]
    return call


@app.get("/api/calls/pending", dependencies=[Depends(agent_only)])
def pending_calls(after: int = 0):
    """The app polls this; calls from the last 2 minutes that it has not seen yet."""
    now = time.time()
    return [c for c in INCOMING if c["id"] > after and now - c["at"] < 120]


@app.get("/api/numbers/lookup", dependencies=[Depends(agent_only)])
def number_lookup(n: str = "", hidden: bool = False):
    """What we know about a number before anyone speaks."""
    return registry.lookup(n, [m for m in _load()["members"] if _visible(m)], hidden=hidden)


@app.get("/api/numbers", dependencies=[Depends(agent_only)])
def numbers_list():
    members = [m for m in _load()["members"] if _visible(m)]
    owners = {norm_phone(p): m["name"] for m in members for p in m.get("phones", [])}
    rows = registry.numbers()
    for r in rows:
        r["owner"] = owners.get(r["number"])
    return rows


class NumberStatus(BaseModel):
    number: str
    status: str
    note: str = ""


@app.post("/api/numbers", dependencies=[Depends(agent_only)])
def number_set(body: NumberStatus):
    if body.status not in ("trusted", "flagged", "seen") or len(norm_phone(body.number)) < 6:
        raise HTTPException(400, detail={"code": "invalid_parameter"})
    return registry.set_status(body.number, body.status, body.note)


@app.get("/api/incidents.csv", dependencies=[Depends(agent_only)])
def incidents_csv():
    return PlainTextResponse(report.to_csv(registry.incidents(5000)), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=kaunbol-call-log.csv"})


@app.get("/api/incidents/{iid}/report", response_class=HTMLResponse, dependencies=[Depends(agent_only)])
def incident_report(iid: int):
    r = registry.incident(iid)
    if not r:
        raise HTTPException(404, detail={"code": "not_found"})
    info = registry.lookup(r["display"] or "", [m for m in _load()["members"] if _visible(m)]) if r["display"] else None
    return report.html(r, info)


# ------------------------------------------------- ask the real customer
class ConfirmReq(BaseModel):
    member_id: str
    caller_number: str = ""
    lang: str = "en"
    guard_id: str = ""


@app.post("/api/confirm", dependencies=[Depends(agent_only)])
async def confirm_request(body: ConfirmReq):
    """Push a 'Is this you on the call?' prompt to the claimed customer's registered phone."""
    m = next((x for x in _load()["members"] if x["id"] == body.member_id and _visible(x)), None)
    if not m:
        raise HTTPException(404, detail={"code": "member_not_found"})
    if registry.device_reported(m["id"]):
        raise HTTPException(409, detail={"code": "device_reported"})
    req = confirm.create(m, body.caller_number, body.lang if body.lang in challenge.WORDS else "en")
    req["guard_id"] = matching_guard(body.guard_id, m["id"])
    link_bot_check(req["guard_id"], confirmation_id=req["id"])
    await refresh_guard(req["guard_id"])
    out = confirm.public(req)
    out["device_url"] = stepup.device_url(req["token"])
    out["sent_to"] = (m.get("phones") or ["registered device"])[0]
    return out


@app.get("/api/confirm/status/{rid}", dependencies=[Depends(agent_only)])
def confirm_status(rid: str):
    req = confirm.by_id(rid)
    if not req:
        raise HTTPException(404, detail={"code": "not_found"})
    return confirm.public(req)


@app.get("/api/confirm/device/{token}", dependencies=[Depends(customer_limit)])
async def confirm_device(token: str):
    req = confirm.by_token(token)
    if not req:
        raise HTTPException(404, detail={"code": "not_found"})
    return confirm.public(req, for_device=True)


class DeviceLanguage(BaseModel):
    lang: str


@app.post("/api/confirm/device/{token}/language", dependencies=[Depends(customer_limit)])
async def confirm_language(token: str, body: DeviceLanguage):
    req = confirm.by_token(token)
    if not req or not req.get("transaction") or req["status"] != "pending" or not transactions.is_current(req):
        raise HTTPException(409, detail={"code": "not_pending"})
    if body.lang not in challenge.WORDS:
        raise HTTPException(422, detail={"code": "invalid_language"})
    confirm.set_language(req, body.lang)
    return confirm.public(req, for_device=True)


def _pending_otp_request(req: dict | None, *, email_required: bool = True) -> dict:
    if (not req or req["status"] != "pending" or not req.get("transaction", {}).get("otp_required")
            or (email_required and req.get("email_delivery") != "accepted") or not transactions.is_current(req)):
        raise HTTPException(409, detail={"code": "otp_unavailable"})
    return req


@app.post("/api/confirm/device/{token}/otp-start", dependencies=[Depends(customer_limit)])
async def otp_start(token: str):
    req = _pending_otp_request(confirm.by_token(token))
    await _send_transaction_otp(req)
    return confirm.public(req, for_device=True)


async def _send_transaction_otp(req: dict):
    if req.get("otp") in {"sending", "sent", "checking", "verified"}:
        return
    if req.get("otp") == "locked" or not stepup.verify_ready() or not req.get("otp_target"):
        raise HTTPException(409, detail={"code": "otp_unavailable"})
    # Verify can reuse a pending code. Isolate one transaction per recipient until that code is consumed.
    target = hashlib.sha256((os.environ["TWILIO_VERIFY_SERVICE_SID"] + req["otp_target"]).encode()).hexdigest()
    if not registry.reserve_otp(target):
        raise HTTPException(409, detail={"code": "otp_wait"})
    req.update(otp="sending", otp_reservation=target)
    try:
        sid = await stepup.start_otp(req["otp_target"])
    except stepup.ProviderError as exc:
        req["otp"] = "locked"
        raise HTTPException(502, detail={"code": str(exc)}) from exc
    _pending_otp_request(confirm.by_id(req["id"]), email_required=False)
    req.update(otp="sent", otp_sid=sid)


class OtpCode(BaseModel):
    code: str = Field(pattern=r"^[0-9]{4,10}$")


@app.post("/api/confirm/device/{token}/otp-check", dependencies=[Depends(customer_limit)])
async def otp_check(token: str, body: OtpCode):
    req = _pending_otp_request(confirm.by_token(token))
    if req.get("otp") != "sent":
        raise HTTPException(409, detail={"code": "otp_unavailable"})
    req["otp"] = "checking"
    req["otp_attempts"] = req.get("otp_attempts", 0) + 1
    try:
        accepted = await stepup.check_otp(req["otp_sid"], body.code)
    except stepup.ProviderError as exc:
        req["otp"] = "locked" if str(exc) == "otp_expired" or req["otp_attempts"] >= 5 else "sent"
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    if accepted:
        registry.clear_otp_reservation(req["otp_reservation"])
    req = _pending_otp_request(confirm.by_token(token))
    if not accepted:
        req["otp"] = "locked" if req["otp_attempts"] >= 5 else "sent"
        raise HTTPException(409, detail={"code": "otp_wrong"})
    req["otp"] = "verified"
    confirm.set_language(req, req["lang"], renew=True)
    return confirm.public(req, for_device=True)


@app.post("/api/confirm/device/{token}/deny", dependencies=[Depends(customer_limit)])
async def confirm_deny(token: str):
    req = confirm.by_token(token)
    allowed = ("pending", "checking") if req and req.get("transaction") else ("pending",)
    if not req or req["status"] not in allowed:
        raise HTTPException(409, detail={"code": "not_pending"})
    req["status"] = "denied"
    if norm_phone(req["caller_number"]):
        registry.set_status(req["caller_number"], "flagged", f"real customer {req['member_name']} denied this call")
    await pause_guard(req.get("guard_id"), "customer_denied")
    return confirm.public(req, for_device=True)


@app.post("/api/confirm/device/{token}/approve", dependencies=[Depends(customer_limit)])
async def confirm_approve(
    token: str,
    file: UploadFile = File(...),
    transcript: str = Form("", max_length=2000),
    demo_scenario: str = Form("genuine"),
):
    """The customer says the phrase on THEIR phone; Eidos checks it is really them."""
    req = confirm.by_token(token)
    if not req or req["status"] != "pending":
        raise HTTPException(409, detail={"code": "not_pending"})
    audio, fname = await _read(file)
    if req.get("transaction"):
        return await _approve_transaction(req, audio, fname, transcript, demo_scenario)
    if req["status"] != "pending":
        raise HTTPException(409, detail={"code": "not_pending"})
    phrase = challenge.check(req["challenge_id"], transcript, consume=True)
    try:
        res = await client.verify(audio, fname, req["speaker_id"], demo_scenario)
    except DetectifError as e:
        return _api_error(e)
    if req["status"] != "pending":
        raise HTTPException(409, detail={"code": "not_pending"})
    identity = res.get("result") or {}
    deepfake = res.get("deepfake") or {}
    ok = (identity.get("decision") == "match" and deepfake.get("verdict") == "real"
          and not any(segment.get("verdict") == "fake" for segment in deepfake.get("segments") or [])
          and phrase.get("status") == "passed")
    req["status"] = "confirmed" if ok else "failed"
    req["result"] = {"decision": identity.get("decision"), "similarity": identity.get("similarity"),
                     "voice": deepfake.get("verdict"), "phrase": phrase.get("status"),
                     "simulated": bool(res.get("simulated"))}
    await refresh_guard(req.get("guard_id"))
    return confirm.public(req, for_device=True)


async def _approve_transaction(req: dict, audio: bytes, filename: str, transcript: str, scenario: str) -> dict:
    if not transactions.is_current(req) or confirm.by_id(req["id"])["status"] != "pending":
        raise HTTPException(409, detail={"code": "not_pending"})
    if req["transaction"].get("otp_required") and req.get("otp") != "verified":
        raise HTTPException(409, detail={"code": "otp_required"})
    req["status"] = "checking"
    result = {"decision": None, "voice": None, "phrase": None, "simulated": client.demo}
    if not client.demo:
        try:
            transcript = await stepup.transcribe(audio, req["lang"])
        except stepup.ProviderError as exc:
            result["error"] = str(exc)
            transcript = ""
    if not transactions.is_current(req) or confirm.by_id(req["id"])["status"] != "checking":
        raise HTTPException(409, detail={"code": "not_pending"})
    phrase = challenge.check(req["challenge_id"], transcript, consume=True, need=challenge.N_WORDS, exact=True)
    result["phrase"] = phrase["status"]
    if phrase["status"] != "passed":
        req.update(status="failed", result=result)
        return confirm.public(req, for_device=True)

    try:
        response = await client.verify(audio, filename, req["speaker_id"], scenario)
    except (DetectifError, httpx.HTTPError):
        response = {}
        result["error"] = "provider_unavailable"
    if not transactions.is_current(req) or confirm.by_id(req["id"])["status"] != "checking":
        raise HTTPException(409, detail={"code": "not_pending"})
    identity = response.get("result") or {}
    voice = response.get("deepfake") or {}
    partial_fake = any(segment.get("verdict") == "fake" for segment in voice.get("segments") or [])
    result.update(decision=identity.get("decision"), voice="fake" if partial_fake else voice.get("verdict"),
                  simulated=client.demo or bool(response.get("simulated")))
    approved = (result["decision"] == "match" and result["voice"] == "real"
                and (client.demo or not result["simulated"]))
    req.update(status="confirmed" if approved else "failed", result=result)
    return confirm.public(req, for_device=True)


class TransactionDetails(BaseModel):
    amount_minor: int = Field(strict=True, ge=1, le=100_000_000)
    merchant: str = Field(min_length=1, max_length=80)
    lang: str = "en"

    @field_validator("merchant", mode="before")
    @classmethod
    def trim_merchant(cls, value):
        return value.strip() if isinstance(value, str) else value


class TransactionStart(TransactionDetails):
    member_id: str


class TransactionChange(TransactionDetails):
    revision: int = Field(strict=True, ge=1)


def _transaction_member(member_id: str) -> dict:
    member = next((m for m in _load()["members"] if m["id"] == member_id and _visible(m) and not m.get("agent")), None)
    if member is None:
        raise HTTPException(404, detail={"code": "member_not_found"})
    return member


def _update_transaction(member: dict, body: TransactionDetails, tx: dict | None = None) -> dict:
    lang = body.lang if body.lang in challenge.WORDS else "en"
    return transactions.issue(member, body.amount_minor, body.merchant, lang, client.demo, tx)


@app.post("/api/transactions", dependencies=[Depends(agent_only)], status_code=201)
async def transaction_start(body: TransactionStart):
    return _update_transaction(_transaction_member(body.member_id), body)


@app.get("/api/transactions/{tid}", dependencies=[Depends(agent_only)])
async def transaction_status(tid: str):
    tx = transactions.get(tid)
    if tx is None:
        raise HTTPException(404, detail={"code": "not_found"})
    return transactions.public(tx)


@app.put("/api/transactions/{tid}", dependencies=[Depends(agent_only)])
async def transaction_update(tid: str, body: TransactionChange):
    tx = transactions.get(tid)
    if tx is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if body.revision != tx["revision"]:
        raise HTTPException(409, detail={"code": "transaction_changed"})
    return _update_transaction(_transaction_member(tx["member_id"]), body, tx)


def _current_transaction(tid: str) -> dict:
    tx = transactions.get(tid)
    if tx is None:
        raise HTTPException(404, detail={"code": "not_found"})
    return tx


@app.post("/api/transactions/{tid}/report-device", dependencies=[Depends(agent_only)])
async def report_transaction_device(tid: str):
    tx = _current_transaction(tid)
    registry.report_lost_device(tx["member_id"])
    confirm.revoke_for_member(tx["member_id"])
    return transactions.public(tx)


@app.get("/api/transactions/{tid}/qr", dependencies=[Depends(agent_only)])
async def transaction_qr(tid: str, revision: int, request: Request):
    tx = _current_transaction(tid)
    if revision != tx["revision"]:
        raise HTTPException(409, detail={"code": "transaction_changed"})
    link = transactions.public(tx)["device_url"]
    if not link:
        raise HTTPException(409, detail={"code": "not_pending"})
    if link.startswith("/"):
        link = str(request.base_url).rstrip("/") + link
    image = qrcode.make(link, image_factory=SvgPathImage)
    image_bytes = io.BytesIO()
    image.save(image_bytes)
    return Response(image_bytes.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.post("/api/transactions/{tid}/sms", dependencies=[Depends(agent_only)])
async def transaction_sms(tid: str, revision: int):
    tx = _current_transaction(tid)
    if revision != tx["revision"]:
        raise HTTPException(409, detail={"code": "transaction_changed"})
    view = transactions.public(tx)
    if not view["device_url"]:
        raise HTTPException(409, detail={"code": "not_pending"})
    if not view["sms_available"]:
        raise HTTPException(409, detail={"code": "sms_unconfigured"})
    if tx["sms"] in ("sending", "accepted", "sms_unknown"):
        return view
    member = _transaction_member(tx["member_id"])
    number = next(iter(member.get("phones") or []), "")
    tx["sms"] = "sending"
    try:
        await stepup.send_link(number, view["device_url"])
        sms = "accepted"
    except stepup.ProviderError as exc:
        sms = str(exc)
    if tx["revision"] == revision:
        tx["sms"] = sms
    return transactions.public(tx)


@app.post("/api/transactions/{tid}/email", dependencies=[Depends(agent_only)])
async def transaction_email(tid: str, revision: int):
    tx = _current_transaction(tid)
    if revision != tx["revision"]:
        raise HTTPException(409, detail={"code": "transaction_changed"})
    view = transactions.public(tx)
    if not view["device_url"] or not view["email_available"]:
        raise HTTPException(409, detail={"code": "email_unavailable"})
    req = confirm.by_id(tx["confirmation_id"])
    if req.get("email_delivery") in {"sending", "accepted", "unknown"}:
        return view
    req["email_delivery"] = "sending"
    try:
        await stepup.email_link(req["email_target"], view["device_url"])
        req["email_delivery"] = "accepted"
    except stepup.ProviderError:
        req["email_delivery"] = "unknown"
    return transactions.public(tx)


@app.post("/api/transactions/{tid}/otp", dependencies=[Depends(agent_only)])
async def transaction_otp(tid: str, revision: int):
    tx = _current_transaction(tid)
    if revision != tx["revision"]:
        raise HTTPException(409, detail={"code": "transaction_changed"})
    req = _pending_otp_request(confirm.by_id(tx["confirmation_id"]), email_required=False)
    await _send_transaction_otp(req)
    return transactions.public(tx)


# ----------------------------------------------- two-way trust (verify the bank)
class AgentFlag(BaseModel):
    agent: bool


@app.put("/api/family/{member_id}/agent", dependencies=[Depends(agent_only)])
def set_agent(member_id: str, body: AgentFlag):
    """Mark an enrolled voice as bank staff, so customers can verify calls from them."""
    with _lock:
        d = _load()
        m = next((x for x in d["members"] if x["id"] == member_id), None)
        if not m:
            raise HTTPException(404, detail={"code": "member_not_found"})
        m["agent"] = body.agent
        _save(d)
    return _public_member(m)


class OutboundReq(BaseModel):
    agent_id: str
    customer_id: str
    lang: str = "en"


@app.post("/api/outbound", dependencies=[Depends(agent_only)])
def outbound_start(body: OutboundReq):
    """The real agent declares: 'I am calling this customer now'."""
    members = [m for m in _load()["members"] if _visible(m)]
    agent = next((m for m in members if m["id"] == body.agent_id and m.get("agent")), None)
    cust = next((m for m in members if m["id"] == body.customer_id), None)
    if not agent or not cust:
        raise HTTPException(404, detail={"code": "member_not_found"})
    s = outbound.start(agent, cust, body.lang if body.lang in challenge.WORDS else "en")
    return {**outbound.public(s), "customer_url": f"/verify?c={cust['id']}"}


@app.get("/api/outbound/{sid}", dependencies=[Depends(agent_only)])
def outbound_status(sid: str):
    s = outbound.get(sid)
    if not s:
        raise HTTPException(404, detail={"code": "not_found"})
    return outbound.public(s)


@app.post("/api/outbound/{sid}/end", dependencies=[Depends(agent_only)])
def outbound_end(sid: str):
    s = outbound.get(sid)
    if s:
        s["status"] = "ended"
    return {"ended": True}


@app.get("/api/whos-calling/{customer_id}", dependencies=[Depends(customer_limit)])
def whos_calling(customer_id: str):
    """The customer's bank app asks: is my bank calling me right now?"""
    s = outbound.active_for(customer_id)
    if not s:
        return {"active": False}
    if not s["phrase"]:
        outbound.new_phrase(s)
    return {"active": True, **outbound.public(s)}


@app.post("/api/whos-calling/{customer_id}/verify", dependencies=[Depends(customer_limit)])
async def whos_calling_verify(
    customer_id: str,
    file: UploadFile = File(...),
    transcript: str = Form("", max_length=2000),
    demo_scenario: str = Form("genuine"),
):
    """The agent says the phrase shown on the customer's phone; Eidos checks it is the enrolled agent."""
    s = outbound.active_for(customer_id)
    if not s:
        raise HTTPException(409, detail={"code": "no_active_call"})
    audio, fname = await _read(file)
    phrase = challenge.check(s["challenge_id"], transcript, consume=True) if s["challenge_id"] else {"status": "unknown"}
    try:
        res = await client.verify(audio, fname, s["agent_speaker"], demo_scenario)
    except DetectifError as e:
        return _api_error(e)
    idn, df = res.get("result") or {}, res.get("deepfake") or {}
    ok = idn.get("decision") == "match" and df.get("verdict") != "fake" and phrase.get("status") == "passed"
    s["result"] = {"verified": ok, "decision": idn.get("decision"), "similarity": idn.get("similarity"),
                   "voice": df.get("verdict"), "phrase": phrase.get("status"), "simulated": bool(res.get("simulated"))}
    if not ok:
        outbound.new_phrase(s)  # a failed attempt burns the phrase
    return {"active": True, **outbound.public(s)}


@app.get("/verify")
def verify_page():
    return FileResponse(FRONTEND / "verify.html")


# ------------------------------------------------------------- accuracy page
BENCH = DATA / "benchmark"
BENCH_RESULTS = "results-demo.json" if client.demo else "results.json"
_benchmark_lock = asyncio.Lock()


@app.get("/api/benchmark")
def benchmark_get():
    results = benchmark.load_results(BENCH, BENCH_RESULTS)
    if results and "metrics" not in results:
        results["metrics"] = benchmark.summarize(results["rows"])
    members = [m for m in _load()["members"] if _visible(m)]
    cases = benchmark.plans(BENCH, members)
    return {"manifest": [item for item, _, _ in cases], "results": results,
            "readiness": {"total": len(cases), "ready": sum(error is None for _, _, error in cases)},
            "mode": "demo" if client.demo else "live"}


@app.post("/api/benchmark/run", dependencies=[Depends(agent_only)])
async def benchmark_run():
    members = [m for m in _load()["members"] if _visible(m)]
    try:
        budget = len(benchmark.load_manifest(BENCH)) if client.demo else int(os.getenv("KAUNBOL_BENCHMARK_MAX_CALLS", "0"))
    except ValueError:
        raise HTTPException(409, detail={"code": "benchmark_budget"})
    if _benchmark_lock.locked():
        raise HTTPException(409, detail={"code": "benchmark_running"})
    async with _benchmark_lock:
        try:
            return await benchmark.run(BENCH, client, members, BENCH_RESULTS, max_calls=budget)
        except ValueError as error:
            raise HTTPException(409, detail={"code": str(error)}) from error


@app.get("/confirm/{token}")
def confirm_page(token: str):
    return FileResponse(FRONTEND / "confirm.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


# --------------------------------------------------------------------- PWA
# The service worker must be served from the site root so its scope covers the whole app.
@app.get("/sw.js")
def service_worker():
    return FileResponse(FRONTEND / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(FRONTEND / "manifest.webmanifest", media_type="application/manifest+json")


@app.post("/share-target")
def share_target_fallback():
    """Normally the service worker handles 'Share to Kaun Bol Raha Hai' from WhatsApp; this is the
    fallback if the worker is not active yet: open the app and let the person upload the clip."""
    return RedirectResponse("/?shared=0", status_code=303)


# ------------------------------------------------------- sample voice clips
# The sample clips are real, consented voices of teammates. They live in data/demo_clips/ (legacy:
# frontend/demo_clips/) and are NEVER served as static files; only this PIN-guarded route returns them.
CLIP_DIRS = (DATA / "demo_clips", FRONTEND / "demo_clips")
SAMPLE_NAMES = ("clip27.wav", "clip30.wav", "scam_tts.wav")
_CLIP_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}\.(wav|mp3|m4a|ogg|webm)$")
_AUDIO_EXT = (".wav", ".mp3", ".m4a", ".ogg", ".opus", ".webm", ".aac", ".flac")


def _clip_path(name: str) -> Path | None:
    if _CLIP_RE.match(name) and ".." not in name:
        for d in CLIP_DIRS:
            p = d / name
            if p.is_file():
                return p
    return None


@app.get("/api/demo-clip/{name}", dependencies=[Depends(agent_only)])
def demo_clip(name: str):
    path = _clip_path(name)
    if path:
        return FileResponse(path)
    raise HTTPException(404, detail={"code": "clip_missing"})


class _SafeStatic(StaticFiles):
    """Static files for the UI only: no audio and nothing under demo_clips, whatever the folder holds."""

    async def get_response(self, path, scope):
        if "demo_clips" in path.replace("\\", "/").split("/") or path.lower().endswith(_AUDIO_EXT):
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


# ------------------------------------------------------------------ frontend
app.mount("/static", _SafeStatic(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
