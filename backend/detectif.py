"""Thin client for the DetectifAI REST API (Aletheia + Eidos).

Live mode calls https://platform.detectif.ai. Demo mode returns clearly
labelled simulated responses with the same shape, so the UI can be shown
when there is no key or no network. Demo mode is never silent: every
simulated response carries "simulated": true.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = os.getenv("DETECTIF_BASE_URL", "https://platform.detectif.ai")
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


@dataclass
class DetectifError(Exception):
    status: int
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.status} {self.code}: {self.message}"


class DetectifClient:
    def __init__(self, api_key: str | None, demo: bool = False):
        self.api_key = api_key
        self.demo = demo or not api_key

    # ---------------------------------------------------------------- helpers
    def _headers(self, idem: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    @staticmethod
    def _idem(data: bytes, *parts: str) -> str:
        # Same audio + same request -> same key, so a retry is never charged twice.
        h = hashlib.sha256(data)
        for p in parts:
            h.update(p.encode())
        return "kb-" + h.hexdigest()[:48]

    async def _post(self, path: str, files: dict, data: dict, idem: str) -> dict:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(path, headers=self._headers(idem), files=files, data=data)
        return self._parse(r)

    @staticmethod
    def _parse(r: httpx.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            raise DetectifError(r.status_code, err.get("code", "http_error"),
                                err.get("message", r.text[:200]))
        return body

    # ------------------------------------------------------------- endpoints
    async def detect(self, audio: bytes, filename: str, scenario: str = "genuine") -> dict:
        if self.demo:
            return _demo_detect(audio, scenario)
        return await self._post(
            "/v1/deepfake/detect",
            files={"file": (filename, audio)},
            data={},
            idem=self._idem(audio, "detect"),
        )

    async def enroll(self, audio: bytes, filename: str, speaker_id: str, display_name: str) -> dict:
        if self.demo:
            return _demo_enroll(speaker_id, display_name)
        return await self._post(
            "/v1/speakers/enroll",
            files={"file": (filename, audio)},
            data={"speaker_id": speaker_id, "display_name": display_name[:128]},
            idem=self._idem(audio, "enroll", speaker_id),
        )

    async def verify(self, audio: bytes, filename: str, speaker_id: str, scenario: str = "genuine",
                     detect_deepfake: bool = True) -> dict:
        """Eidos verify; with detect_deepfake=true it is identity + liveness in one call."""
        if self.demo:
            return _demo_verify(audio, speaker_id, scenario)
        return await self._post(
            "/v1/speakers/verify",
            files={"file": (filename, audio)},
            data={"speaker_id": speaker_id, "detect_deepfake": "true" if detect_deepfake else "false"},
            idem=self._idem(audio, "verify", speaker_id, str(detect_deepfake)),
        )

    async def delete_speaker(self, speaker_id: str) -> None:
        if self.demo:
            return
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.delete(f"/v1/speakers/{speaker_id}", headers=self._headers())
        if r.status_code not in (200, 404):
            self._parse(r)

    async def feedback(self, operation_id: str, correct: bool, label: str | None) -> dict:
        if self.demo:
            return {"object": "operation.feedback", "simulated": True}
        payload: dict[str, Any] = {"correct": correct}
        if label in ("real", "fake"):
            payload["label"] = label
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
            r = await c.post(f"/v1/operations/{operation_id}/feedback",
                             headers=self._headers(), json=payload)
        return self._parse(r)

    async def credits(self) -> dict | None:
        if self.demo:
            return None
        try:
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as c:
                r = await c.get("/v1/credits", headers=self._headers())
            return self._parse(r)
        except Exception:
            return None


# ------------------------------------------------------------------ demo mode
# Scenarios are picked explicitly in the UI (only visible in demo mode), so a
# demo never pretends a simulated verdict came from the real model.

def _seed(audio: bytes) -> float:
    return int(hashlib.sha256(audio).hexdigest()[:8], 16) / 0xFFFFFFFF


def _segments(total: float, base: float, spliced: bool) -> list[dict]:
    segs, t, i = [], 0.0, 0
    while t < max(total - 1, 1):
        end = min(t + 4.0, total)
        s = base
        if spliced and i in (2, 3):
            s = 0.91 - 0.04 * i
        s = round(min(max(s + 0.02 * ((i % 3) - 1), 0.0), 1.0), 4)
        segs.append({"start": round(t, 2), "end": round(end, 2), "score": s,
                     "verdict": "fake" if s >= 0.5 else "real"})
        t += 2.0
        i += 1
    return segs


def _demo_detect(audio: bytes, scenario: str) -> dict:
    r = _seed(audio)
    dur = round(8 + r * 8, 1)
    fake = scenario == "cloned"
    spliced = scenario == "spliced"
    base = 0.86 + r * 0.1 if fake else 0.05 + r * 0.06
    segs = _segments(dur, base, spliced)
    score = round(sum(s["score"] for s in segs) / len(segs), 4)
    verdict = "fake" if fake else "real"
    return {
        "simulated": True,
        "request_id": "req_demo",
        "operation_id": "op_demo_" + uuid.uuid4().hex[:10],
        "object": "deepfake.detection",
        "result": {
            "verdict": verdict,
            "label": "synthetic" if fake else "genuine",
            "is_synthetic": fake,
            "score": score,
            "duration_seconds": dur,
            "scored_seconds": dur,
            "segments": segs,
        },
        "model": {"name": "aletheia", "version": "demo"},
        "latency_ms": 420,
    }


def _demo_enroll(speaker_id: str, display_name: str) -> dict:
    return {
        "simulated": True,
        "object": "speaker.enrollment",
        "speaker": {"speaker_id": speaker_id, "display_name": display_name, "samples": 1},
        "quality": {"speech_seconds": 18.2, "duration_seconds": 20.0},
        "model": {"name": "eidos", "version": "demo"},
    }


def _demo_verify(audio: bytes, speaker_id: str, scenario: str) -> dict:
    det = _demo_detect(audio, scenario)
    r = _seed(audio)
    wrong = scenario == "wrong_person"
    # A good clone of the right person can still "match" on identity —
    # exactly why the app needs the deepfake check as well.
    sim = round(0.12 + r * 0.15, 4) if wrong else round(0.66 + r * 0.12, 4)
    return {
        "simulated": True,
        "request_id": "req_demo",
        "operation_id": "op_demo_" + uuid.uuid4().hex[:10],
        "object": "speaker.verification",
        "speaker_id": speaker_id,
        "result": {
            "decision": "no_match" if wrong else "match",
            "similarity": sim,
            "duration_seconds": det["result"]["duration_seconds"],
        },
        "deepfake": {**det["result"], "operation_id": det["operation_id"]},
        "model": {"name": "eidos", "version": "demo"},
        "latency_ms": 610,
    }
