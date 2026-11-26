# Backend

[Project overview](../README.md) · [Architecture](../docs/ARCHITECTURE.md) · [Provider setup](../BANK_SETUP.md)

Python 3.12 / FastAPI serves the browser assets and APIs from one process.

## Run from the repository root

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
$env:DEMO_MODE = "1"
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8003 --workers 1
```

Open `http://127.0.0.1:8003`. Demo mode does not require paid provider credentials. Use [BANK_SETUP.md](../BANK_SETUP.md) for live transcription, DetectifAI, SMTP, Twilio, and customer HTTPS configuration.

## Code map

- `app.py`: request validation, agent access, customer routes, enrollment, checks, transaction orchestration, and static assets.
- `detectif.py`: DetectifAI HTTP client, idempotency keys, and labelled demo responses.
- `live.py`: live audio sessions and guard updates; `botguard.py` manages challenge signals.
- `transactions.py`, `confirm.py`, `challenge.py`: transaction revisions, confirmation lifecycles, and spoken challenges.
- `stepup.py`: Google transcription, SMTP delivery, Twilio messaging and Verify.
- `registry.py`: SQLite incidents, number status, device reports, and OTP reservations.
- `identify.py`, `verdict.py`, `scam_script.py`, `fingerprint.py`: identity resolution, risk signals, script matching, and replay detection.
- `benchmark.py`, `benchmark_gen.py`, `report.py`: evaluation and evidence exports.

## Test

From `backend/`:

```text
python -m unittest discover -s tests
```

Tests exercise demo/mocked providers, transaction replacement during a check, challenge matching, missing provider verdicts, agent PIN protection, and OTP behavior. These tests do not establish live provider accuracy or real-device compatibility. Private recordings and runtime databases are excluded from Git.
