# Kaun Bol Raha Hai? Final pitch (Track 03: The Trust Layer)

**Slot:** 7 minutes = **4 min presentation + 3 min Q&A**
**Marks:** Model Usage 30 · Real-world Usefulness 30 · Working Demo 25 · Presentation 15

> **The line judges must remember:** "Others detect the clone. **We hold the money until the *real* customer says yes.**"

---

## Part 1: The 4-minute presentation

**Roles:** **SPEAKER** talks · **DRIVER** runs the agent desk on the laptop · **CUSTOMER** holds the second phone or browser window (ideally Person A)
**Ready before you start:** `http://127.0.0.1:8003` open, banner **Live**, Person A's number cleared in **Numbers & log**, the cloned-call sample ready, the customer window open.

| Clock | Section | Screen | SPEAKER says (about 130 words per minute) |
|---|---|---|---|
| **0:00–0:20** | Hook | Title / agent desk | "Every team here can tell you whether a voice sounds fake. But detection is an arms race: the clones get better every month. So we asked a different question. Not *does this voice sound real*, but **should this money move?** Our answer: **the money waits until the real customer says yes.**" |
| **0:20–0:45** | Problem | — | "A UAE bank sent **35 million dollars** after one call with a cloned director's voice. In India, fake officials on the phone took **₹1,935 crore** in 2024. Bank agents can't hear a clone, and today they still approve requests on the voice on the line. We built for the person who has to make that call: **the bank agent.**" |
| **0:45–1:00** | What it is | Agent desk | "Kaun Bol is a trust layer for bank and fintech call centres. It checks every caller with **DetectifAI Aletheia** (is the voice real?) and **Eidos** (is it the right customer?), and **holds** risky requests until the real customer confirms on their own phone." |
| **1:00–1:40** | **Demo 1: the attack** | DRIVER: **Transaction review** → Person A, **₹50,000** → **Place on hold** → **Open live call guard** → play the clone | "A caller on Person A's **registered** number wants ₹50,000 moved now. The number looks right, so most systems would trust it. The request starts **held**. We stream the call live to Aletheia… **AI voice detected in about five seconds**, while he's still talking. Eidos agrees: **not Person A**." |
| **1:40–2:10** | **Demo 2: real customer says no** | CUSTOMER opens the request → exact amount and payee → **Deny** · desk: **Denied** | "Now imagine the clone was perfect and fooled every model. It doesn't matter: the request goes to the **real** customer's phone, showing the **exact amount and payee**. They tap **Deny**, and the agent sees it instantly. **The money never moved.**" |
| **2:10–2:50** | **Demo 3: real customer says yes** | New request → CUSTOMER: **Verify to approve** → says the **3 words** → **Approve this exact transaction** · desk: **Approved** · DRIVER edits the amount → old link **replaced** | "When it *is* the customer, they say a fresh three-word phrase on **their own phone**. Eidos confirms it's their voice, Aletheia confirms it's human, and **only this exact amount** is approved. Change one rupee and the old approval is dead, so an attacker can't reuse a 'yes'." |
| **2:50–3:25** | Depth: why it's more than a detector | Optional: mixed-clip timeline, or the "Is my bank calling me?" page | "Three things most detectors miss. **One:** we read DetectifAI's segment timeline. In one test the overall verdict said *real*, and we still found **seven seconds of AI speech** spliced into a real recording. **Two:** a real recording played back can't say a fresh random phrase. **Three:** it works both ways: customers can check whether a 'bank officer' calling **them** is genuine." |
| **3:25–3:45** | Proof | Numbers slide or the Accuracy card | "Everything you saw runs live on DetectifAI. On our labelled set, **10 of 10 checks were correct**: genuine voices matched at 0.92 to 0.96, an impostor scored 0.52, and an AI voice 0.05. It's a small set, so every result says *evidence, not proof*." |
| **3:45–4:00** | Close | — | "FastAPI, live WebSocket streaming, English, Hindi and Kannada, and 81 automated tests. **Others detect the clone. We hold the money until the real customer says yes.** Thank you." |

**If you're running over time:** cut **Depth** (2:50–3:25) down to the first point only. **Never cut Demo 2 or Demo 3.**
**If the demo breaks:** say "Network dropped, switching to our labelled demo mode" and run the same steps on the demo tab. Don't apologise twice.

---

## Part 2: The 3-minute Q&A playbook

**Rules:** answer in **about 15–20 seconds**, **one number** if you have one, then stop. With 3 minutes, a short answer means more questions and more chances to score. If you don't know, say *"We haven't measured that yet; here's how we would"*, never guess.

**Who answers:** **A** models and accuracy · **B** live streaming, architecture and cost · **C** transactions, security and the customer channel. The SPEAKER passes the question by name.

| If they ask… | Who | Say this (about 15–20 s) |
|---|---|---|
| **Why both Aletheia and Eidos?** | A | "They catch different attacks. A good clone of the right customer can **pass** Eidos; a real human impostor **passes** Aletheia. So we call Eidos verify with `detect_deepfake=true`: one call, both answers on the same audio." |
| **What if the clone is perfect?** | C | "Then detection may miss it, which is exactly why the money waits for the **real customer on their registered phone**. A clone isn't holding that phone and can't say a phrase it's never heard." |
| **Why not just OTP?** | C | "Fraudsters talk victims into reading OTPs out. Here the customer tells the caller nothing: they answer the **bank** on their own device, Eidos verifies **their voice there**, and the approval covers only that exact amount. OTP is supported as an extra factor." |
| **How accurate is it?** | A | "10 of 10 on our labelled set: real voices 0.00–0.02, AI voice 0.997, genuine matches 0.92–0.96, impostor 0.52. It's a small set, so next we run our 45-case phone-quality benchmark: 8 kHz, G.711, Opus and noise." |
| **How fast? What does it cost?** | B | "First verdict at about **5 seconds** of speech. About 2 credits per second for deepfake, and one identity check per approval, roughly **5 cents**. That's tiny next to one fraud loss." |
| **Does it scale to millions of customers?** | B | "Yes. Identity is **one-to-one**: the account or number picks one voiceprint, so the cost per call stays flat however many customers there are." |
| **Stolen phone?** | C | "Approval still needs the customer's **voice** on that phone, a fresh phrase, and a synthesis check. A **lost-phone report** revokes every open request for that customer." |
| **Replay a real recording?** | C | "The phrase is random, single-use and expires in 90 seconds. A recording can't say words it's never heard." |
| **How does it plug into a real bank?** | B | "The phone system (Twilio, Exotel) calls one endpoint with the caller's number and streams the audio. The customer page sits behind the bank app's login, and the payment system reads our **held/approved/denied** status." |
| **Privacy and consent?** | C | "Voices are enrolled only with consent, and deleting a customer deletes their voiceprint at DetectifAI. Clips are never served as files, and the agent desk is PIN-locked." |
| **What doesn't work yet?** | A | "There's no real payment rail; mobile HTTPS and regional speech on physical phones need device tests; and the 45-case benchmark is planned, not run. We'd rather show what's measured." |
| **What was hardest?** | B | "The spliced audio. The overall score said *real*, so we had to read the segment timeline to find the seven AI seconds. That taught us not to trust a single number." |
| **Did you build this?** | Any | Answer truthfully, in your own words. Each person explains their own part, and offers to open the code. |
| **Why Track 03?** | A | "Track 03 is about checking every caller: real voice, right person. We do both, and add the step banks actually need: **holding the money until the real customer confirms.**" |

---

## Part 3: Fundamentals every member must know cold

### The problem, in one sentence
Voice cloning means **"it sounds like them" is no longer proof of identity**, but banks still act on phone calls.

### What each model does (our level of understanding; we don't claim to know their internals)
- **Aletheia (deepfake detection):** takes audio and returns **real or fake** plus a **0–1 score**, where higher means more likely AI-generated. It scores **overlapping windows** of a few seconds each, which is how we see *which seconds* look synthetic. **DetectifAI's verdict decides** real or fake, not our own threshold.
- **Eidos (speaker verification):** you **enrol** a person once (15–60 s of speech, up to 5 samples). Later you **verify** a new clip against that one person and get **match / no_match** plus a **similarity from −1 to 1**. It is **not** a percentage. DetectifAI makes the decision.
- **Verification vs identification:** Eidos answers *"is this the claimed person?"* (**1:1**). It doesn't search a crowd (*"who is this?"*). That's why we first find **which customer** the caller claims to be, from the account or registered number.

### Why both models
| Attack | Aletheia | Eidos | Caught by |
|---|---|---|---|
| AI clone of the customer | **fake** | may **match** | Aletheia |
| Real human impostor | real | **no match** | Eidos |
| Replayed real recording | real | match | **Fresh phrase** (can't say new words) |
| Perfect clone, spoofed number | might pass | might pass | **Held money + real customer on their own phone** |

### Our key design decisions, and why
1. **Held by default:** the cheapest safe state for money is *not moving*.
2. **Second channel:** the attacker controls the call, not the customer's registered phone.
3. **Approval bound to the exact amount, payee and version:** a "yes" can't be reused for a different transaction.
4. **Segment analysis:** a single overall score can hide a short AI splice.
5. **Evidence, not proof:** every result shows scores, a next step and a report, and nothing approves without an **explicit real-voice result**.

### How the live call works (for B)
Mic or PC audio → browser **AudioWorklet** at **16 kHz, 100 ms frames** → our **FastAPI WebSocket** → DetectifAI **`/v1/stream`** (`checks=deepfake,verify`). We get an **early verdict at about 5 s of speech**, then updates. The **API key stays on our server**, never in the browser.

### Numbers to know by heart
| | |
|---|---|
| Early verdict on a cloned call | **5.5 s** (score 0.999) |
| AI voice vs real voice | **0.997–1.00** vs **0.000–0.017** |
| Genuine vs impostor vs AI (Eidos) | **0.92–0.96** vs **0.52** vs **0.05** |
| Test set | **10 / 10** correct (small set) |
| Splice test | overall **real 0.32**, segments **0.998–1.000** fake at 6–14 s |
| Tests | **81** automated |
| Cost | about **2 credits/s** deepfake, about **$0.05** per identity check |

### Tech stack in one breath
"**Python FastAPI** backend, **SQLite** for number memory and evidence, **DetectifAI Aletheia and Eidos** over REST and live WebSocket, a browser **AudioWorklet** for 16 kHz streaming, an installable **PWA**, **Twilio Verify** for SMS OTP, **SMTP** for email, **QR** links, **English, Hindi and Kannada**, and **81 tests**."

---

## Rehearsal checklist
- [ ] Run the full 4 minutes **twice with a timer**, once on venue Wi-Fi.
- [ ] Run the **hold → deny → approve → edit** flow **live once**. Each approval costs about 545 credits; check the balance first.
- [ ] Each member answers **5 random Q&A questions** in under 20 seconds.
- [ ] The demo-mode tab is open as a fallback.
- [ ] **Clear** Person A's number before the run.
