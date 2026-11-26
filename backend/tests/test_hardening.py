import os
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

os.environ["DEMO_MODE"] = "1"
os.environ.setdefault("KAUNBOL_DATA", tempfile.mkdtemp())

import app
import preflight


class HardeningTests(unittest.TestCase):
    def test_customer_limit_is_shared_and_expires(self):
        app._customer_requests.clear()
        with TestClient(app.app) as client, patch("app.monotonic", return_value=100):
            for _ in range(30):
                self.assertEqual(client.get("/api/whos-calling/no-session").status_code, 200)
            limited = client.get("/api/confirm/device/bad-token")
            self.assertEqual(limited.status_code, 429)
            self.assertGreater(int(limited.headers["retry-after"]), 0)
            self.assertEqual(client.get("/api/status").status_code, 200)
        with TestClient(app.app) as client, patch("app.monotonic", return_value=161):
            self.assertEqual(client.get("/api/whos-calling/no-session").status_code, 200)
        app._customer_requests.clear()

    def test_startup_exposes_single_worker_warning(self):
        with self.assertLogs("uvicorn.error", level="WARNING") as logs, TestClient(app.app):
            pass
        self.assertIn("Single worker only", logs.output[0])

    def test_preflight_checks_live_https_pin_and_never_prints_fixture_key(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "family.json").write_text(json.dumps({
                "members": [{"agent": True, "samples": 1}, {"agent": False, "samples": 1}],
            }))
            clips = data / "demo_clips"
            clips.mkdir()
            for name in ("clip27.wav", "clip30.wav", "scam_tts.wav"):
                (clips / name).touch()
            key = "fixture-key-for-preflight"

            def respond(request):
                if request.url.path == "/v1/credits":
                    self.assertEqual(request.headers["authorization"], f"Bearer {key}")
                    return httpx.Response(200, json={"balance": 10})
                return httpx.Response(200, json={"mode": "live", "pin_required": True})

            env = {"KAUNBOL_DATA": directory, "DETECTIF_API_KEY": key, "AGENT_PIN": "fixture", "DEMO_MODE": "0"}
            output = io.StringIO()
            stub = httpx.Client(transport=httpx.MockTransport(respond))
            with patch.dict(os.environ, env), patch("preflight.httpx.Client", return_value=stub), redirect_stdout(output):
                self.assertTrue(preflight.check("http://localhost:8000", "https://fixture.test"))
            self.assertIn("Provider credits: 10", output.getvalue())
            self.assertNotIn(key, output.getvalue())
