import os
import tempfile
import unittest

os.environ["DEMO_MODE"] = "1"
os.environ.setdefault("KAUNBOL_DATA", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

import app as kb  # noqa: E402
import outbound  # noqa: E402
from verdict import assess  # noqa: E402

REAL = {"verdict": "real", "score": 0.02, "segments": []}
NO_SCRIPT = {"analysed": False, "hits": {}, "level": "none"}
WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + bytes(24) + b"data" + bytes(4)


class ActionRiskTests(unittest.TestCase):
    def test_same_clean_call_is_green_for_balance_but_needs_confirmation_for_contact_change(self):
        ok = {"decision": "match"}
        self.assertEqual(assess(REAL, ok, NO_SCRIPT, claimed=True, action="balance")["level"], "low")
        v = assess(REAL, ok, NO_SCRIPT, claimed=True, action="contact_change")
        self.assertEqual(v["level"], "medium")
        self.assertEqual(v["next_steps"][0], "confirm_on_device")

    def test_action_never_lowers_risk(self):
        v = assess({"verdict": "fake", "score": 0.99, "segments": []}, None, NO_SCRIPT, claimed=False, action="balance")
        self.assertEqual(v["level"], "high")


class TwoWayTrustTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(kb.app)
        with kb._lock:
            d = kb._load()
            d["members"] = [
                {"id": "ag", "name": "Agent Priya", "relation": "", "speaker_id": "kb-ag", "samples": 1, "created_at": 0,
                 "phones": [], "simulated": True, "agent": True},
                {"id": "cu", "name": "Ravi", "relation": "", "speaker_id": "kb-cu", "samples": 1, "created_at": 0,
                 "phones": [], "simulated": True},
            ]
            kb._save(d)
        outbound._sessions.clear()

    def test_no_active_call_means_not_your_bank(self):
        self.assertEqual(self.c.get("/api/whos-calling/cu").json(), {"active": False})

    def test_real_agent_call_verifies(self):
        s = self.c.post("/api/outbound", json={"agent_id": "ag", "customer_id": "cu"}).json()
        self.assertEqual(s["status"], "active")
        w = self.c.get("/api/whos-calling/cu").json()
        self.assertTrue(w["active"])
        self.assertEqual(w["agent_name"], "Agent Priya")
        out = self.c.post("/api/whos-calling/cu/verify", files={"file": ("a.wav", WAV)},
                          data={"transcript": w["phrase"]}).json()
        self.assertTrue(out["result"]["verified"])
        self.assertTrue(self.c.get(f"/api/outbound/{s['id']}").json()["result"]["verified"])

    def test_wrong_phrase_fails_and_burns_it(self):
        self.c.post("/api/outbound", json={"agent_id": "ag", "customer_id": "cu"})
        w = self.c.get("/api/whos-calling/cu").json()
        out = self.c.post("/api/whos-calling/cu/verify", files={"file": ("a.wav", WAV)},
                          data={"transcript": "hello hello"}).json()
        self.assertFalse(out["result"]["verified"])
        self.assertNotEqual(out["phrase"], w["phrase"])

    def test_only_staff_can_start_outbound(self):
        r = self.c.post("/api/outbound", json={"agent_id": "cu", "customer_id": "ag"})
        self.assertEqual(r.status_code, 404)

    def test_ended_call_is_not_active(self):
        s = self.c.post("/api/outbound", json={"agent_id": "ag", "customer_id": "cu"}).json()
        self.c.post(f"/api/outbound/{s['id']}/end")
        self.assertFalse(self.c.get("/api/whos-calling/cu").json()["active"])


if __name__ == "__main__":
    unittest.main()
