"""Check live demo prerequisites without printing credentials or spending on voice analysis."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MIN_FREE = 2 * 1024 ** 3


def enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def check(url: str, public_url: str | None) -> bool:
    results = []

    def record(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" ({detail})" if detail else ""))

    data = Path(os.getenv("KAUNBOL_DATA", ROOT / "data"))
    key = os.getenv("DETECTIF_API_KEY")
    record("DetectifAI key present", bool(key))
    record("Live mode configured", bool(key) and not enabled("DEMO_MODE"))
    with httpx.Client(timeout=5.0, follow_redirects=False) as http:
        if key:
            base = os.getenv("DETECTIF_BASE_URL", "https://platform.detectif.ai")
            try:
                response = http.get(base.rstrip("/") + "/v1/credits", headers={"Authorization": f"Bearer {key}"})
                record("DetectifAI authentication", response.is_success, f"HTTP {response.status_code}")
                if response.is_success:
                    balance = response.json()
                    if isinstance(balance, dict):
                        for field in ("balance", "credits", "available", "remaining"):
                            value = balance.get(field)
                            if type(value) in (int, float):
                                print(f"INFO  Provider credits: {value}")
                                break
            except (httpx.HTTPError, ValueError) as error:
                record("DetectifAI authenticated credit check", False, type(error).__name__)
        else:
            record("DetectifAI authentication", False, "key missing")

        try:
            response = http.get(url.rstrip("/") + "/api/status")
            status = response.json()
            reachable = response.is_success and isinstance(status, dict) and status.get("mode") in ("live", "demo")
        except (httpx.HTTPError, ValueError):
            reachable, status = False, {}
        record("Local /api/status", reachable)
        if reachable:
            record("Running server is in live mode", status["mode"] == "live", status["mode"])
        else:
            parsed = urlsplit(url)
            if parsed.hostname in ("localhost", "127.0.0.1"):
                with socket.socket() as probe:
                    try:
                        probe.bind(("127.0.0.1", parsed.port or 80))
                        record("Local port available for startup", True)
                    except OSError:
                        record("Local port available for startup", False)

        public = enabled("PUBLIC") or bool(public_url)
        record("Agent PIN for public access", not public or bool(os.getenv("AGENT_PIN")))
        if public_url:
            try:
                response = http.get(public_url.rstrip("/") + "/api/status") if urlsplit(public_url).scheme == "https" else None
                remote_status = response.json() if response is not None and response.is_success else {}
                ok = isinstance(remote_status, dict) and remote_status.get("mode") == "live" and remote_status.get("pin_required") is True
            except (httpx.HTTPError, ValueError):
                ok = False
            record("Public HTTPS reaches the live app with PIN enabled", ok)

    try:
        members = json.loads((data / "family.json").read_text(encoding="utf-8")).get("members", [])
    except (OSError, ValueError):
        members = []
    enrolled = [m for m in members if m.get("samples") and not m.get("simulated")]
    record("At least one live-enrolled agent", any(m.get("agent") for m in enrolled))
    record("At least one live-enrolled customer", any(not m.get("agent") for m in enrolled))
    sample_dir = data / "demo_clips"
    missing = [name for name in ("clip27.wav", "clip30.wav", "scam_tts.wav") if not (sample_dir / name).is_file()]
    record("Demo clips present", not missing, f"{3 - len(missing)}/3")
    drive_path = data.resolve()
    while not drive_path.exists():
        drive_path = drive_path.parent
    free = shutil.disk_usage(drive_path).free
    record("At least 2 GiB free for app data", free >= MIN_FREE, f"{free / 1024 ** 3:.2f} GiB free")
    return all(results)


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--public-url", default=os.getenv("PUBLIC_URL"))
    args = parser.parse_args()
    raise SystemExit(0 if check(args.url, args.public_url) else 1)
