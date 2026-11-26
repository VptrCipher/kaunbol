import os
import tempfile
import unittest

from fastapi.testclient import TestClient

os.environ["DEMO_MODE"] = "1"
os.environ.setdefault("KAUNBOL_DATA", tempfile.mkdtemp())

import app
from botguard import Event, pause_reason, transition
from live import _wav


def next_event(socket, kind):
    for _ in range(20):
        message = socket.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"Missing event {kind}")


class BotStateTests(unittest.TestCase):
    def test_state_requires_human_resume(self):
        running = Event("running", "started", 0)
        paused = transition(running, "pause", "deepfake_high", 1)
        self.assertEqual(paused.state, "paused")
        self.assertEqual(transition(paused, "low_risk", "", 2), paused)
        human = transition(paused, "handover", "", 3)
        self.assertEqual(human.state, "handed_over")
        self.assertEqual(transition(human, "resume", "", 4), Event("running", "agent_override", 4))

    def test_reasons_cover_voice_identity_and_other_high_risk(self):
        for reason, expected in [("voice_fake", "deepfake_high"), ("identity_no_match", "identity_no_match"),
                                 ("number_flagged", "call_high_risk")]:
            self.assertEqual(pause_reason({"level": "high", "reasons": [reason]}), expected)
        self.assertIsNone(pause_reason({"level": "medium", "reasons": ["action_high_impact"]}))


class BotIntegrationTests(unittest.TestCase):
    def test_cloned_demo_pauses_and_logs_handoff_and_resume(self):
        with TestClient(app.app) as client:
            with client.websocket_connect("/ws/live?bot=1&hidden=1&scenario=cloned") as ws:
                self.assertEqual(next_event(ws, "bot_state")["state"], "running")
                next_event(ws, "ready")
                ws.send_bytes(b"\x00\x00" * 16000 * 4)
                event = next_event(ws, "bot_state")
                self.assertEqual(event["reason"], "deepfake_high")
                ws.send_json({"type": "bot_control", "action": "step"})
                ws.send_json({"type": "bot_control", "action": "handover"})
                self.assertEqual(next_event(ws, "bot_state")["state"], "handed_over")
                ws.send_json({"type": "bot_control", "action": "resume"})
                self.assertEqual(next_event(ws, "bot_state")["reason"], "agent_override")
                ws.send_json({"type": "bot_control", "action": "step"})
                self.assertFalse(next_event(ws, "bot_gate")["can_step"])
                ws.send_json({"type": "stop"})
                next_event(ws, "completed")
                result = next_event(ws, "risk")
                self.assertEqual(len(result["bot_events"]), 4)
                incident = result["incident_id"]
            report = client.get(f"/api/incidents/{incident}/report")
            self.assertIn("Voice bot event history", report.text)
            self.assertIn("agent override", report.text)
            self.assertIn("deepfake high", client.get("/api/incidents.csv").text.replace("_", " "))

    def test_review_requires_live_signals_replay_and_device_confirmation(self):
        with TestClient(app.app) as client:
            audio = _wav(b"\x00\x00" * 16000 * 6)
            files = {"file": ("fixture.wav", audio)}
            member = client.post("/api/family/enroll", files=files, data={"name": "All checks test"}).json()["member"]
            try:
                with client.websocket_connect(f"/ws/live?bot=1&hidden=1&member={member['id']}") as ws:
                    guard = next_event(ws, "bot_state")["id"]
                    next_event(ws, "ready")
                    ws.send_json({"type": "bot_control", "action": "step"})
                    self.assertFalse(next_event(ws, "bot_gate")["can_step"])
                    ws.send_bytes(b"\x00\x00" * 16000 * 6)
                    self.assertEqual(next_event(ws, "verification")["result"]["decision"], "match")
                    replay = client.post("/api/challenge", json={"guard_id": guard}).json()
                    answer = client.post(f"/api/challenge/{replay['id']}/answer",
                                         data={"final": "true", "transcript": replay["phrase"]})
                    self.assertEqual(answer.json()["result"], "passed")
                    ws.send_json({"type": "bot_control", "action": "step"})
                    self.assertFalse(next_event(ws, "bot_gate")["can_step"])
                    confirmation = client.post("/api/confirm", json={
                        "guard_id": guard, "member_id": member["id"]}).json()
                    url = "/api/confirm/device/" + confirmation["token"]
                    phrase = client.get(url).json()["phrase"]
                    reply = client.post(url + "/approve", files=files, data={"transcript": phrase})
                    self.assertEqual(reply.json()["status"], "confirmed")
                    self.assertTrue(next_event(ws, "bot_gate")["can_step"])
                    ws.send_json({"type": "bot_control", "action": "step"})
                    self.assertEqual(next_event(ws, "bot_line")["line"], "review")
                    self.assertFalse(next_event(ws, "bot_gate")["can_step"])
                    ws.send_json({"type": "stop"})
                    next_event(ws, "completed")
            finally:
                client.delete(f"/api/family/{member['id']}")

    def test_linked_replay_failure_and_customer_denial_pause_bot(self):
        with TestClient(app.app) as client:
            audio = _wav(b"\x00\x00" * 16000 * 5)
            member = client.post("/api/family/enroll", files={"file": ("fixture.wav", audio)},
                                 data={"name": "Bot guard test customer"}).json()["member"]
            try:
                with client.websocket_connect(f"/ws/live?bot=1&hidden=1&member={member['id']}") as ws:
                    guard = next_event(ws, "bot_state")["id"]
                    next_event(ws, "ready")
                    ch = client.post("/api/challenge", json={"guard_id": guard}).json()
                    answer = client.post(f"/api/challenge/{ch['id']}/answer", data={"final": "true"})
                    self.assertEqual(answer.json()["result"], "failed_phrase")
                    self.assertEqual(next_event(ws, "bot_state")["reason"], "replay_failed")
                    ws.send_json({"type": "bot_control", "action": "resume"})
                    next_event(ws, "bot_state")
                    req = client.post("/api/confirm", json={"guard_id": guard, "member_id": member["id"]}).json()
                    self.assertEqual(client.post(f"/api/confirm/device/{req['token']}/deny").status_code, 200)
                    self.assertEqual(next_event(ws, "bot_state")["reason"], "customer_denied")
                    ws.send_json({"type": "stop"})
                    next_event(ws, "completed")
                    next_event(ws, "risk")
            finally:
                client.delete(f"/api/family/{member['id']}")
