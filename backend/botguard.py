"""State transitions for the simulated bank voice bot."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    state: str
    reason: str
    at: float


def transition(current: Event, action: str, reason: str, at: float) -> Event:
    if action == "pause" and current.state == "running":
        return Event("paused", reason, at)
    if action == "handover" and current.state in ("running", "paused"):
        return Event("handed_over", "agent_handover", at)
    if action == "resume" and current.state in ("paused", "handed_over"):
        return Event("running", "agent_override", at)
    return current


def pause_reason(verdict: dict) -> str | None:
    if verdict["level"] != "high":
        return None
    if "voice_fake" in verdict["reasons"]:
        return "deepfake_high"
    if "identity_no_match" in verdict["reasons"]:
        return "identity_no_match"
    return "call_high_risk"
