"""Combine the three signals into one honest, explainable risk level.

Signals:
  1. Voice liveness  — Aletheia verdict + per-segment scores
  2. Identity        — Eidos decision vs. the family member the caller claims to be
  3. Script          — scam-script detector on the transcript

The output is reason codes and next-step codes; the UI turns them into
English / Hindi / Kannada text. A score is evidence, not proof, so every
level — even "low" — comes with a next step.
"""
from __future__ import annotations

HIGH_IMPACT_ACTIONS = {"contact_change", "payment", "card"}
BORDERLINE_LOW, BORDERLINE_HIGH = 0.40, 0.60  # only asks for step-up on a "real" verdict; never approves


def assess(deepfake: dict | None, identity: dict | None, script: dict, claimed: bool,
           number: str | None = None, rep: dict | None = None, action: str | None = None) -> dict:
    """number: None (not given) | 'match' | 'mismatch' (claims to be X from a number X never used) | 'unknown'."""
    reasons: list[str] = []
    points = 0  # internal ordering only, never shown as a "probability"

    # 1. Voice liveness -------------------------------------------------------
    spliced_ranges: list[list[float]] = []
    if deepfake is None:
        reasons.append("voice_not_checked")
    elif "error" in deepfake:
        reasons.append("voice_check_failed")
        points += 1
    else:
        verdict = deepfake.get("verdict")
        score = float(deepfake.get("score") or 0)
        fake_segs = [s for s in deepfake.get("segments") or [] if s.get("verdict") == "fake"
                     or (s.get("verdict") is None and float(s.get("score", 0)) >= 0.5)]
        if verdict == "fake":
            reasons.append("voice_fake")
            points += 10
        elif fake_segs:
            # Overall "real" but some windows look synthetic: possible splice of a
            # real recording with generated words ("...send money now").
            reasons.append("voice_spliced")
            spliced_ranges = [[s["start"], s["end"]] for s in fake_segs]
            points += 5
        elif BORDERLINE_LOW <= score <= BORDERLINE_HIGH:
            reasons.append("voice_borderline")
            points += 3
        else:
            reasons.append("voice_real")

    # 2. Identity -------------------------------------------------------------
    if not claimed:
        reasons.append("identity_not_checked")
    elif identity is None:
        reasons.append("identity_check_failed")
        points += 1
    elif identity.get("decision") == "no_match":
        reasons.append("identity_no_match")
        points += 8
    else:
        reasons.append("identity_match")

    # 3. Script ---------------------------------------------------------------
    lvl = script.get("level", "none")
    if not script.get("analysed"):
        reasons.append("script_not_analysed")
    else:
        reasons.append(f"script_{lvl}")
        points += {"high": 4, "medium": 2, "low": 1}.get(lvl, 0)

    # 4. Caller number (like a bank matching caller ID to the account) -------
    if number == "match":
        reasons.append("number_match")
    elif number == "mismatch":
        reasons.append("number_mismatch")
        points += 3
    elif number == "unknown":
        reasons.append("number_unknown")
        points += 1

    # 5. Number memory: what we knew about this number before anyone spoke -----
    st = (rep or {}).get("status")
    if st == "flagged":
        reasons.append("number_flagged")
        points += 5
    elif st == "hidden":
        reasons.append("number_hidden")
        points += 2
    elif st == "first_time":
        reasons.append("number_first_time")
        points += 1
    if (rep or {}).get("international"):
        reasons.append("number_international")
        points += 2

    verified = "identity_match" in reasons
    voice_ok = "voice_real" in reasons
    # A real human reading a scam script is still a scam (fake "police", "digital arrest"):
    # no AI needed. If the caller is not verified as the person they claim to be, a strong
    # script alone is enough for high risk.
    scripted_stranger = lvl == "high" and not verified
    # Verified family member + scam script: could be coercion or a real emergency.
    if verified and voice_ok and lvl in ("high", "medium"):
        reasons.append("real_but_scripted")

    # Level -------------------------------------------------------------------
    if "voice_fake" in reasons or "identity_no_match" in reasons:
        level = "high"
    elif st == "flagged" and not (verified and voice_ok):
        level = "high"  # this number was caught before
    elif scripted_stranger or (lvl in ("high", "medium") and (number == "mismatch" or st == "hidden"
                                                             or (rep or {}).get("international"))):
        level = "high"
        reasons.append("scripted_unverified")
    elif points >= 3 or "real_but_scripted" in reasons:
        level = "medium"
    else:
        level = "low"

    # 6. What is being asked for ------------------------------------------------
    # Same voice, different stakes: a balance enquiry is not a change of mobile number.
    # High-impact requests need the real customer's confirmation on their own phone.
    high_impact = action in HIGH_IMPACT_ACTIONS
    if high_impact:
        reasons.append("action_high_impact")
        if level == "low":
            level = "medium"

    # Next steps --------------------------------------------------------------
    steps: list[str] = []
    asks_money = "money" in script.get("hits", {}) or "credentials" in script.get("hits", {})
    if level == "high":
        steps += ["dont_pay", "hang_up_callback", "ask_safeword", "report_1930", "warn_family"]
    elif "real_but_scripted" in reasons:
        steps += ["ask_safeword", "ask_private_question", "hang_up_callback", "dont_pay"]
    elif level == "medium":
        steps += ["ask_safeword", "hang_up_callback", "dont_pay"]
    else:
        steps += ["callback_if_money"] if asks_money or not script.get("analysed") else ["looks_ok"]
    if "voice_not_checked" in reasons or "voice_check_failed" in reasons:
        steps.insert(0, "retry_longer")
    if high_impact:
        steps.insert(0, "confirm_on_device")

    return {
        "level": level,
        "reasons": reasons,
        "next_steps": steps,
        "offer_safeword": level in ("medium", "high"),
        "spliced_ranges": spliced_ranges,
    }
