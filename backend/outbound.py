"""Two-way trust: let the CUSTOMER verify that "the bank" calling them is real.

The most common scam in India runs the other way round: a fake "bank officer"
calls the customer. So when a real agent calls a customer, the agent opens an
outbound session ("I'm calling customer X now"). On the customer's phone, the
bank app can then check:

  - no active session for this customer  -> "Your bank is NOT calling you. Hang up."
  - active session                       -> the agent says a fresh phrase; Eidos
                                            verifies it is the enrolled agent's voice
                                            (and Aletheia that it is not synthetic)
"""
from __future__ import annotations

import secrets
import time

import challenge

TTL = 15 * 60  # an outbound call session lasts at most 15 minutes
_sessions: dict[str, dict] = {}


def start(agent: dict, customer: dict, lang: str) -> dict:
    now = time.time()
    for k in [k for k, v in _sessions.items() if now - v["created"] > 3600]:
        _sessions.pop(k, None)
    # one active outbound call per customer
    for s in _sessions.values():
        if s["customer_id"] == customer["id"] and s["status"] == "active":
            s["status"] = "ended"
    sid = secrets.token_hex(6)
    _sessions[sid] = {
        "id": sid, "created": now, "status": "active", "lang": lang,
        "agent_id": agent["id"], "agent_name": agent["name"], "agent_speaker": agent["speaker_id"],
        "customer_id": customer["id"], "customer_name": customer["name"],
        "challenge_id": None, "phrase": None, "result": None,
    }
    return _sessions[sid]


def _alive(s: dict) -> dict:
    if s["status"] == "active" and time.time() - s["created"] > TTL:
        s["status"] = "ended"
    return s


def get(sid: str) -> dict | None:
    s = _sessions.get(sid)
    return _alive(s) if s else None


def active_for(customer_id: str) -> dict | None:
    for s in sorted(_sessions.values(), key=lambda x: -x["created"]):
        if s["customer_id"] == customer_id and _alive(s)["status"] == "active":
            return s
    return None


def new_phrase(s: dict) -> str:
    ch = challenge.issue(s["lang"])
    s["challenge_id"], s["phrase"] = ch["id"], ch["phrase"]
    return ch["phrase"]


def public(s: dict) -> dict:
    return {"id": s["id"], "status": s["status"], "agent_name": s["agent_name"], "customer_name": s["customer_name"],
            "started": int(s["created"]), "phrase": s["phrase"], "result": s["result"]}
