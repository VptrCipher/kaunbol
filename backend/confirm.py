"""Ask the real customer, on their registered phone, while the call is happening.

Every other check asks "does this voice sound like the customer?". A perfect
clone can eventually pass that. This asks a different question: is the real
customer actually on this call? The bank pushes a prompt to the customer's
registered device:

  - "Not me"       -> the agent panel turns red at once, the number is flagged
  - "Yes, it's me" -> the customer says a fresh phrase on their own phone and
                      Eidos verifies them there (a second, independent channel)
  - no answer      -> step-up verification

In production the prompt is a push notification / SMS link to the registered
device. Here the link opens /confirm/<token> in any browser.
"""
from __future__ import annotations

import secrets
import time

import challenge
import stepup

TTL = 120
_by_id: dict[str, dict] = {}
_by_token: dict[str, dict] = {}


def _mask(number: str) -> str:
    digits = "".join(c for c in number if c.isdigit())
    return ("•••• " + digits[-4:]) if len(digits) >= 4 else "hidden number"


def create(member: dict, caller_number: str, lang: str, ttl: int = TTL) -> dict:
    now = time.time()
    for d in (_by_id, _by_token):
        for k in [k for k, v in d.items() if now - v["created"] > 3600]:
            d.pop(k, None)
    ch = challenge.issue(lang)
    req = {
        "id": secrets.token_hex(6),
        "token": secrets.token_urlsafe(16),  # unguessable: only the registered device gets the link
        "member_id": member["id"], "member_name": member["name"], "speaker_id": member["speaker_id"],
        "caller_number": caller_number, "caller_masked": _mask(caller_number),
        "challenge_id": ch["id"], "phrase": ch["phrase"], "lang": lang,
        "created": now, "expires_at": now + ttl, "status": "pending", "result": None,
    }
    _by_id[req["id"]] = req
    _by_token[req["token"]] = req
    return req


def _expire(req: dict) -> dict:
    if req["status"] in ("pending", "checking") and time.time() >= req["expires_at"]:
        req["status"] = "expired"
    return req


def revoke_for_member(member_id: str) -> None:
    for req in _by_id.values():
        if req["member_id"] == member_id and req["status"] in ("pending", "checking"):
            req.update(status="revoked", result=None)


def set_language(req: dict, lang: str, *, renew: bool = False) -> None:
    if req["lang"] != lang or renew:
        ch = challenge.issue(lang)
        req.update(lang=lang, phrase=ch["phrase"], challenge_id=ch["id"])


def by_token(token: str) -> dict | None:
    r = _by_token.get(token)
    return _expire(r) if r else None


def by_id(rid: str) -> dict | None:
    r = _by_id.get(rid)
    return _expire(r) if r else None


def public(req: dict, for_device: bool = False) -> dict:
    if for_device and req["status"] in ("revoked", "superseded"):
        return {"status": req["status"], "lang": req["lang"]}
    out = {"id": req["id"], "status": req["status"], "member_name": req["member_name"],
           "caller_masked": req["caller_masked"], "created": int(req["created"]),
           "expires_in": max(0, int(req["expires_at"] - time.time())), "result": req["result"]}
    if "transaction" in req:
        out["transaction"] = req["transaction"]
    if for_device:
        waits_for_otp = req.get("transaction", {}).get("otp_required") and req.get("otp") != "verified"
        out["phrase"] = "" if waits_for_otp else req["phrase"]
        out["lang"] = req["lang"]
        if req.get("transaction", {}).get("otp_required"):
            out["otp"] = req.get("otp", "idle")
            out["email_link_sent"] = req.get("email_delivery") == "accepted"
            out["otp_available"] = bool(req.get("otp_target") and stepup.verify_ready())
    else:
        out["token"] = req["token"]
    return out
