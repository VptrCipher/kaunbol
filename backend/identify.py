"""Work out who the caller claims to be, without the user picking from a list.

This mirrors how a bank does it: you never search every stored voice. You find
the ONE account the caller claims (caller ID / account number), then do a 1:1
voice check against that account's enrolled voice.

Order of evidence:
  1. Picked by hand            -> that member
  2. Caller's phone number     -> the member who owns that saved number
  3. Name spoken on the call   -> "Papa, it's Rahul" -> the member called Rahul
"""
from __future__ import annotations

import re

def norm_phone(s: str | None) -> str:
    """Compare on the last 10 digits, so +91 98450 12345 == 9845012345."""
    digits = re.sub(r"\D", "", s or "")
    return digits[-10:] if len(digits) >= 10 else digits


def by_number(number: str, members: list[dict]) -> dict | None:
    n = norm_phone(number)
    if len(n) < 6:
        return None
    for m in members:
        if any(norm_phone(p) == n for p in m.get("phones", [])):
            return m
    return None


def _word_in(word: str, text: str) -> bool:
    word = word.strip().lower()
    if not word:
        return False
    if re.fullmatch(r"[a-z0-9 ]+", word):
        return re.search(rf"\b{re.escape(word)}\b", text) is not None
    return word in text  # Devanagari / Kannada: plain substring


def by_transcript(transcript: str, members: list[dict]) -> dict | None:
    """Self-identification in the call: the caller says their own name ("it's Rahul").

    Relation words are deliberately ignored: callers use them to address the
    listener ("Papa, ..."), so they say nothing about who is calling.
    Only an unambiguous single name match counts.
    """
    text = (transcript or "").lower()
    if not text:
        return None
    named = [m for m in members if _word_in(m["name"], text)
             or any(_word_in(part, text) for part in m["name"].split()
                    if len(part) >= 3 and part.lower() != "person")]
    return named[0] if len(named) == 1 else None


def number_status(number: str, member: dict | None, members: list[dict]) -> str | None:
    """Compare the calling number with what we know: None if no number was given."""
    if not norm_phone(number):
        return None
    owner = by_number(number, members)
    if member is None:
        return "match" if owner else "unknown"
    if owner and owner["id"] == member["id"]:
        return "match"
    if member.get("phones"):
        return "mismatch"  # claims to be X, but X has never used this number ("this is my new number")
    return None  # no saved number for X: nothing to compare


def resolve(picked_id: str, number: str, transcript: str, members: list[dict]) -> tuple[dict | None, str | None]:
    """Returns (member, how) where how is 'picked' | 'number' | 'transcript' | None."""
    if picked_id:
        m = next((x for x in members if x["id"] == picked_id), None)
        if m:
            return m, "picked"
    m = by_number(number, members)
    if m:
        return m, "number"
    m = by_transcript(transcript, members)
    if m:
        return m, "transcript"
    return None, None
