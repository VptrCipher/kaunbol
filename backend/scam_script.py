"""Scam-script detector.

Looks at *what* is being said (the transcript), not *how* it sounds.
Voice-clone scams in India follow a small set of scripts: an emergency,
pressure to pay now, an authority figure, secrecy, and a new number.
This is a transparent keyword/pattern scorer — every point it adds is
shown to the user, so nothing is a black box.

Covers English, romanised Hindi (Hinglish), Hindi (Devanagari) and
Kannada. Non-English patterns should be reviewed by native speakers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# category -> (weight, [patterns])
PATTERNS: dict[str, tuple[int, list[str]]] = {
    "money": (25, [
        r"\bsend (me )?(the )?money\b", r"\btransfer\b", r"\bupi\b", r"\bgpay\b", r"\bphone ?pe\b",
        r"\bpaytm\b", r"\bqr code\b", r"\brupees?\b", r"\brs\.? ?\d", r"₹", r"\blakh\b", r"\bthousand\b",
        r"\bpaise\b", r"\bpaisa\b", r"\bbhej(o|do|na)\b", r"\bpayment\b", r"\bbail\b",
        "पैसे", "पैसा", "भेजो", "भेज दो", "रुपये", "यूपीआई", "पेमेंट",
        "ಹಣ", "ದುಡ್ಡು", "ಕಳುಹಿಸು", "ಕಳಿಸು", "ರೂಪಾಯಿ", "ಯುಪಿಐ",
    ]),
    "urgency": (20, [
        r"\burgent(ly)?\b", r"\bright now\b", r"\bimmediately\b", r"\bquick(ly)?\b", r"\bhurry\b",
        r"\bno time\b", r"\bwithin (\d+|ten|five|an?) (minutes?|hours?)\b", r"\bjaldi\b", r"\bturant\b",
        r"\babhi ke abhi\b", r"\bfauran\b",
        "जल्दी", "तुरंत", "अभी के अभी", "फौरन",
        "ಬೇಗ", "ತುರ್ತು", "ಈಗಲೇ", "ತಕ್ಷಣ",
    ]),
    "authority": (20, [
        r"\bpolice\b", r"\barrest(ed)?\b", r"\bcbi\b", r"\bcustoms\b", r"\bcourier\b", r"\bnarcotics\b",
        r"\bdigital arrest\b", r"\bcase (has been )?(filed|registered)\b", r"\bofficer\b", r"\bjail\b",
        r"\bthana\b", r"\bgiraftar\b", r"\bfir\b",
        "पुलिस", "गिरफ्तार", "थाना", "जेल", "अफसर",
        "ಪೊಲೀಸ್", "ಅರೆಸ್ಟ್", "ಜೈಲು", "ಠಾಣೆ",
    ]),
    "emergency": (15, [
        r"\baccident\b", r"\bhospital\b", r"\binjur(ed|y)\b", r"\bsurgery\b", r"\boperation\b",
        r"\bkidnap(ped)?\b", r"\btrouble\b", r"\bemergency\b", r"\bmusibat\b",
        "अस्पताल", "एक्सीडेंट", "हादसा", "मुसीबत", "ऑपरेशन",
        "ಆಸ್ಪತ್ರೆ", "ಅಪಘಾತ", "ಆಕ್ಸಿಡೆಂಟ್", "ತೊಂದರೆ",
    ]),
    "secrecy": (20, [
        r"\b(don'?t|do not|dont) tell (anyone|anybody|mom|dad|papa|mummy|mumma|amma|appa)\b", r"\bkeep (this|it) (a )?secret\b",
        r"\bbetween us\b", r"\bkisi ko (mat|na) bata(na|o)?\b", r"\bkisi ko kuch mat\b",
        "किसी को मत बताना", "किसी को मत बताओ", "किसी को न बताना",
        "ಯಾರಿಗೂ ಹೇಳಬೇಡ", "ಯಾರಿಗೂ ಹೇಳ್ಬೇಡ",
    ]),
    "new_number": (15, [
        r"\bnew number\b", r"\bthis is my (new|other) number\b", r"\bphone (is )?(broken|lost|switched off)\b",
        r"\bnaya number\b", r"\bdusra number\b",
        "नया नंबर", "दूसरा नंबर", "फोन खराब",
        "ಹೊಸ ನಂಬರ್", "ಬೇರೆ ನಂಬರ್",
    ]),
    "credentials": (25, [
        r"\botp\b", r"\bpin\b", r"\bcvv\b", r"\bpassword\b", r"\bcard number\b", r"\baadhaar\b",
        r"\bshare (the )?code\b",
        "ओटीपी", "पिन", "पासवर्ड", "आधार",
        "ಒಟಿಪಿ", "ಪಿನ್", "ಪಾಸ್ವರ್ಡ್", "ಆಧಾರ್",
    ]),
}

COMPILED = {
    cat: (w, [re.compile(p, re.IGNORECASE) for p in pats]) for cat, (w, pats) in PATTERNS.items()
}


@dataclass
class ScriptResult:
    score: int = 0  # 0-100
    level: str = "none"  # none | low | medium | high
    hits: dict[str, list[str]] = field(default_factory=dict)
    analysed: bool = False

    def as_dict(self) -> dict:
        return {"score": self.score, "level": self.level, "hits": self.hits, "analysed": self.analysed}


def analyse(transcript: str | None) -> ScriptResult:
    text = (transcript or "").strip()
    if not text:
        return ScriptResult()
    res = ScriptResult(analysed=True)
    total = 0
    for cat, (weight, pats) in COMPILED.items():
        found: list[str] = []
        for p in pats:
            m = p.search(text)
            if m and m.group(0).strip() not in found:
                found.append(m.group(0).strip())
        if found:
            res.hits[cat] = found[:4]
            total += weight
    # The classic combination: money + (urgency or secrecy) is the core of the script.
    if "money" in res.hits and ("urgency" in res.hits or "secrecy" in res.hits):
        total += 10
    res.score = min(total, 100)
    res.level = (
        "high" if res.score >= 60 else
        "medium" if res.score >= 35 else
        "low" if res.score > 0 else "none"
    )
    return res
