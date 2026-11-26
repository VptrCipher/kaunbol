"""Replay check: "repeat after me".

A scammer can replay a REAL recording of your son (an old voice note, an
Instagram reel). Aletheia says "real", Eidos says "match", and nothing looks
wrong. A recording cannot say a phrase it has never heard, so we show a fresh
random 3-word phrase that the caller must say back within 90 seconds.

  - replayed recording  -> wrong words          (this check)
  - live voice cloning  -> right words, AI voice (Aletheia on the answer)
  - someone else        -> right words, no match (Eidos on the answer)
"""
from __future__ import annotations

import secrets
import time
import unicodedata
from difflib import SequenceMatcher

WORDS = {
    "en": ["mango", "river", "tiger", "window", "pencil", "garden", "rocket", "yellow", "blanket", "coconut",
           "ladder", "butter", "candle", "monkey", "orange", "silver", "temple", "guitar", "pillow", "rainbow",
           "button", "camel", "doctor", "forest", "hammer", "jacket", "kitten", "lemon", "market", "pepper"],
    "hi": ["आम", "नदी", "बाघ", "खिड़की", "बगीचा", "नारियल", "मोमबत्ती", "बंदर", "चाँदी", "मंदिर",
           "तकिया", "किताब", "कुर्सी", "पतंग", "दरवाज़ा", "सूरज", "बादल", "चिड़िया", "घड़ी", "हाथी",
           "नींबू", "टमाटर", "गुलाब", "पहाड़", "रेलगाड़ी", "चश्मा", "छतरी", "मछली", "तारा", "दूध"],
    "kn": ["ಮಾವು", "ನದಿ", "ಹುಲಿ", "ಕಿಟಕಿ", "ತೋಟ", "ತೆಂಗಿನಕಾಯಿ", "ಕೋತಿ", "ಬೆಳ್ಳಿ", "ದೇವಸ್ಥಾನ", "ದಿಂಬು",
           "ಪುಸ್ತಕ", "ಕುರ್ಚಿ", "ಗಾಳಿಪಟ", "ಬಾಗಿಲು", "ಸೂರ್ಯ", "ಮೋಡ", "ಹಕ್ಕಿ", "ಗಡಿಯಾರ", "ಆನೆ", "ನಿಂಬೆ",
           "ಟೊಮೆಟೊ", "ಗುಲಾಬಿ", "ಬೆಟ್ಟ", "ರೈಲು", "ಕನ್ನಡಕ", "ಛತ್ರಿ", "ಮೀನು", "ನಕ್ಷತ್ರ", "ಹಾಲು", "ಹೂವು"],
}
TTL = 90  # seconds: long enough to ask and answer, too short to fake a recording offline
N_WORDS = 3
NEED = 2  # words that must be heard (speech-to-text drops words now and then)

_active: dict[str, dict] = {}


def _norm(s: str) -> str:
    # Indic vowel signs and viramas are marks, not letters for regex \w.
    s = s.casefold().replace("\u200c", "").replace("\u200d", "")
    s = unicodedata.normalize("NFC", s)
    return "".join(
        c if c.isalnum() or unicodedata.category(c).startswith("M") else " " for c in s
    )


def issue(lang: str, guard_id: str | None = None) -> dict:
    words_list = WORDS.get(lang, WORDS["en"])
    rng = secrets.SystemRandom()
    words = rng.sample(words_list, N_WORDS)
    cid = secrets.token_urlsafe(9)
    now = time.time()
    for k in [k for k, v in _active.items() if v["expires"] + TTL < now]:
        _active.pop(k, None)
    _active[cid] = {"words": words, "expires": now + TTL, "used": False, "lang": lang, "guard_id": guard_id}
    return {"id": cid, "phrase": " ".join(words), "words": words, "expires_in": TTL}


def take_guard(cid: str) -> str | None:
    return _active.get(cid, {}).pop("guard_id", None)


def _heard_words(words: list[str], transcript: str, lang: str) -> list[str]:
    tokens = dict.fromkeys(_norm(transcript).split())
    normalized = {word: _norm(word).strip() for word in words}
    heard = []
    for word, token in normalized.items():
        if token in tokens:
            heard.append(word)
            del tokens[token]
    if lang == "en":
        for word, token in normalized.items():
            if word in heard or not tokens:
                continue
            match = max(tokens, key=lambda t: SequenceMatcher(None, token, t).ratio())
            if SequenceMatcher(None, token, match).ratio() >= 0.75:
                heard.append(word)
                del tokens[match]
    return [word for word in words if word in heard]


def check(cid: str, transcript: str, consume: bool, need: int = NEED, exact: bool = False) -> dict:
    """Did the caller say the phrase? consume=True makes it single-use."""
    c = _active.get(cid)
    if not c:
        return {"status": "unknown"}
    if c["used"]:
        return {"status": "used"}
    if time.time() > c["expires"]:
        return {"status": "expired"}
    heard = _heard_words(c["words"], transcript or "", "" if exact else c["lang"])
    if exact:
        expected = _norm(" ".join(c["words"])).split()
        ok = _norm(transcript or "").split() in (expected, expected * 2)
    else:
        ok = len(heard) >= need
    if consume or ok:
        c["used"] = True
    return {"status": "passed" if ok else "not_heard", "heard": heard, "words": c["words"], "need": need}
