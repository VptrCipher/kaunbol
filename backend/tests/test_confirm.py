import os
import tempfile
import unittest

os.environ["DEMO_MODE"] = "1"
os.environ.setdefault("KAUNBOL_DATA", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

import app as kb  # noqa: E402
import confirm  # noqa: E402
import registry  # noqa: E402

WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + bytes(24) + b"data" + bytes(4)


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(kb.app)
        with kb._lock:
            d = kb._load()
            d["members"] = [{"id": "m1", "name": "Asha", "relation": "", "speaker_id": "kb-asha", "samples": 1,
                             "created_at": 0, "phones": ["+91 90000 77777"], "simulated": True}]
            kb._save(d)

    def test_deny_flags_the_calling_number(self):
        r = self.c.post("/api/confirm", json={"member_id": "m1", "caller_number": "+91 81111 22222"}).json()
        self.assertEqual(r["status"], "pending")
        token = r["device_url"].split("/")[-1]
        dev = self.c.get(f"/api/confirm/device/{token}").json()
        self.assertIn("phrase", dev)
        self.c.post(f"/api/confirm/device/{token}/deny")
        self.assertEqual(self.c.get(f"/api/confirm/status/{r['id']}").json()["status"], "denied")
        self.assertEqual(registry.lookup("8111122222", [])["status"], "flagged")

    def test_approve_with_phrase_on_registered_phone(self):
        r = self.c.post("/api/confirm", json={"member_id": "m1", "caller_number": "+91 90000 77777"}).json()
        token = r["device_url"].split("/")[-1]
        phrase = confirm.by_token(token)["phrase"]
        out = self.c.post(f"/api/confirm/device/{token}/approve", files={"file": ("a.wav", WAV)},
                          data={"transcript": phrase}).json()
        self.assertEqual(out["status"], "confirmed")
        self.assertEqual(out["result"]["decision"], "match")

    def test_token_is_needed(self):
        self.assertEqual(self.c.get("/api/confirm/device/not-a-token").status_code, 404)


if __name__ == "__main__":
    unittest.main()
