import os
import tempfile
import unittest
from unittest import mock

os.environ["DEMO_MODE"] = "1"
os.environ["KAUNBOL_DATA"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

import app as kb  # noqa: E402


class AgentPinTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(kb.app)
        kb._fails.clear()  # the lockout counters are process-wide; start every test clean

    def test_open_when_no_pin_configured(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": ""}):
            self.assertEqual(self.c.get("/api/family").status_code, 200)
            self.assertFalse(self.c.get("/api/status").json()["pin_required"])

    def test_agent_routes_need_the_pin(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            for path in ("/api/family", "/api/history", "/api/numbers", "/api/incidents.csv"):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 401, path)
                self.assertEqual(r.json()["detail"]["code"], "invalid_pin")
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "0000"}).status_code, 401)
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "4821"}).status_code, 200)
            # the PIN is accepted in the header only; a URL (?pin=) must not work
            self.assertEqual(self.c.get("/api/incidents.csv?pin=4821").status_code, 401)
            self.assertEqual(self.c.get("/api/incidents.csv", headers={"X-Agent-Pin": "4821"}).status_code, 200)

    def test_customer_side_stays_open(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            self.assertTrue(self.c.get("/api/status").json()["pin_required"])
            self.assertEqual(self.c.get("/api/whos-calling/nobody").json(), {"active": False})
            self.assertEqual(self.c.get("/api/confirm/device/bad-token").status_code, 404)  # not 401
            self.assertEqual(self.c.get("/verify").status_code, 200)

    def test_wrong_guesses_lock_out_then_recover_on_expiry(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            for i in range(5):
                self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": f"000{i}"}).status_code, 401)
            r = self.c.get("/api/family", headers={"X-Agent-Pin": "4821"})  # even the right PIN is refused while locked
            self.assertEqual(r.status_code, 429)
            self.assertEqual(r.json()["detail"]["code"], "too_many_attempts")
            self.assertEqual(r.headers["retry-after"], "60")
            kb._fails.clear()  # simulates the window passing
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "4821"}).status_code, 200)

    def test_missing_pin_is_not_counted_as_a_guess(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            for _ in range(20):  # the app fires several calls before it asks for the PIN
                self.assertEqual(self.c.get("/api/family").status_code, 401)
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "4821"}).status_code, 200)

    def test_success_clears_earlier_wrong_guesses(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            for _ in range(4):
                self.c.get("/api/family", headers={"X-Agent-Pin": "9999"})
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "4821"}).status_code, 200)
            for _ in range(4):
                self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "9999"}).status_code, 401)

    def test_forwarded_address_only_trusted_behind_a_proxy(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821", "TRUST_PROXY": ""}):
            for i in range(5):  # spoofing X-Forwarded-For must not dodge the lockout
                self.c.get("/api/family", headers={"X-Agent-Pin": "0", "X-Forwarded-For": f"10.0.0.{i}"})
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "4821", "X-Forwarded-For": "10.9.9.9"}).status_code, 429)
        kb._fails.clear()
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821", "TRUST_PROXY": "1"}):
            for _ in range(5):
                self.c.get("/api/family", headers={"X-Agent-Pin": "0", "X-Forwarded-For": "10.0.0.1"})
            self.assertEqual(self.c.get("/api/family", headers={"X-Agent-Pin": "4821", "X-Forwarded-For": "10.0.0.2"}).status_code, 200)

    def test_live_socket_pin_goes_in_the_subprotocol_not_the_url(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            with self.assertRaises(Exception):  # a ?pin= in the URL no longer opens the socket
                with self.c.websocket_connect("/ws/live?pin=4821"):
                    pass
            with self.assertRaises(Exception):  # wrong PIN in the subprotocol
                with self.c.websocket_connect("/ws/live", subprotocols=["kb." + b"0000".hex()]):
                    pass
            with self.c.websocket_connect("/ws/live", subprotocols=["kb." + b"4821".hex()]) as ws:
                self.assertEqual(ws.accepted_subprotocol, "kb." + b"4821".hex())  # echoed, or browsers drop the socket

    def test_live_socket_needs_the_pin(self):
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            with self.assertRaises(Exception):
                with self.c.websocket_connect("/ws/live"):
                    pass

    def test_sample_clips_are_never_static_and_need_the_pin(self):
        clips = kb.DATA / "demo_clips"
        clips.mkdir(exist_ok=True)
        (clips / "voice.wav").write_bytes(b"RIFFfake")
        with mock.patch.dict(os.environ, {"AGENT_PIN": "4821"}):
            for p in ("/static/demo_clips/voice.wav", "/static/demo_clips/clip27.wav", "/static/demo_clips/",
                      "/static/anything.wav", "/static/%2e%2e/data/demo_clips/voice.wav"):
                self.assertEqual(self.c.get(p).status_code, 404, p)
            self.assertEqual(self.c.get("/api/demo-clip/voice.wav").status_code, 401)
            ok = self.c.get("/api/demo-clip/voice.wav", headers={"X-Agent-Pin": "4821"})
            self.assertEqual((ok.status_code, ok.content), (200, b"RIFFfake"))
            for bad in ("..%2fapp.py", "app.py", "..wav", "nope.wav"):
                self.assertEqual(self.c.get("/api/demo-clip/" + bad, headers={"X-Agent-Pin": "4821"}).status_code, 404, bad)
        self.assertEqual(self.c.get("/static/app.js").status_code, 200)  # the UI itself is still served

    def test_pwa_files_served(self):
        self.assertEqual(self.c.get("/sw.js").status_code, 200)
        m = self.c.get("/manifest.webmanifest").json()
        self.assertEqual(m["share_target"]["action"], "/share-target")
        self.assertEqual(self.c.post("/share-target").status_code, 200)  # fallback redirect lands on the app


if __name__ == "__main__":
    unittest.main()
