import os
import tempfile
import unittest

os.environ["DEMO_MODE"] = "1"
os.environ["KAUNBOL_DATA"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

import app as kb  # noqa: E402


class IncomingTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(kb.app)

    def test_twilio_form_and_hidden(self):
        r = self.c.post("/api/calls/incoming", data={"From": "+919000011111", "CallSid": "CA1"}).json()
        self.assertFalse(r["hidden"])
        self.assertEqual(r["info"]["status"], "first_time")
        self.assertEqual(r["source"], "twilio")
        h = self.c.post("/api/calls/incoming", json={"from": "anonymous"}).json()
        self.assertTrue(h["hidden"])
        pend = self.c.get("/api/calls/pending?after=0").json()
        self.assertEqual([p["id"] for p in pend][-2:], [r["id"], h["id"]])

    def test_check_requires_number(self):
        r = self.c.post("/api/check", files={"file": ("a.wav", b"RIFF0000WAVE")}, data={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"]["code"], "number_required")


if __name__ == "__main__":
    unittest.main()
