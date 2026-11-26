"""One-worker state for simulated bank transactions and version-bound approvals."""
from __future__ import annotations

import secrets
import time

import confirm
import registry
import stepup

RETENTION = 3600
_transactions: dict[str, dict] = {}


def get(tid: str) -> dict | None:
    tx = _transactions.get(tid)
    if tx and time.time() - tx["updated"] < RETENTION:
        return tx
    return None


def issue(member: dict, amount_minor: int, merchant: str, lang: str, simulated_checks: bool,
          tx: dict | None = None) -> dict:
    now = time.time()
    if tx is None:
        for tid, old in tuple(_transactions.items()):
            if now - old["updated"] >= RETENTION:
                del _transactions[tid]
        tx = {"id": secrets.token_urlsafe(12), "revision": 0, "member_id": member["id"]}

    revision = tx["revision"] + 1
    otp_required = stepup.otp_enabled()
    req = confirm.create(member, "", lang, ttl=300 if otp_required else 90)
    if otp_required:
        req["email_target"] = member.get("email", "")
        req["otp_target"] = stepup.phone_number(next(iter(member.get("phones") or []), ""))
    if registry.device_reported(member["id"]):
        req["status"] = "revoked"
    req["transaction"] = {
        "id": tx["id"], "revision": revision, "merchant": merchant, "amount_minor": amount_minor,
        "simulated_checks": simulated_checks,
        "otp_required": otp_required,
    }
    old = confirm.by_id(tx.get("confirmation_id", ""))
    if old is not None:
        old["status"] = "superseded"
    tx.update(req["transaction"], confirmation_id=req["id"], updated=now, sms=None)
    _transactions[tx["id"]] = tx
    return public(tx)


def is_current(req: dict) -> bool:
    tx = get(req["transaction"]["id"])
    return (tx is not None and tx["confirmation_id"] == req["id"]
            and not registry.device_reported(req["member_id"]))


def public(tx: dict) -> dict:
    req = confirm.by_id(tx["confirmation_id"])
    status = req["status"] if req else "expired"
    return {
        "id": tx["id"], "revision": tx["revision"], "member_id": tx["member_id"],
        "merchant": tx["merchant"], "amount_minor": tx["amount_minor"],
        "simulated_checks": tx["simulated_checks"],
        "status": {"confirmed": "approved", "denied": "denied"}.get(status, "held"),
        "verification": status,
        "expires_in": confirm.public(req)["expires_in"] if req else 0,
        "device_url": stepup.device_url(req["token"]) if status == "pending" else None,
        "device_reported": registry.device_reported(tx["member_id"]),
        "sms_available": not tx["otp_required"] and stepup.twilio_ready(),
        "sms": tx.get("sms"),
        "otp_required": tx["otp_required"],
        "email_available": bool(req and tx["otp_required"] and stepup.email_ready() and req.get("email_target")),
        "otp_available": bool(req and tx["otp_required"] and stepup.verify_ready() and req.get("otp_target")),
        "email_delivery": req.get("email_delivery") if req else None,
        "otp": req.get("otp") if req else None,
        "result": req["result"] if req else None,
    }
