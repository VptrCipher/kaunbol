import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

os.environ["DEMO_MODE"] = "1"
os.environ.setdefault("KAUNBOL_DATA", tempfile.mkdtemp())

import app as kb
import confirm
import transactions

DEMO_AUDIO = b"demo audio fixture"


class TransactionTests(unittest.TestCase):
    def setUp(self):
        config = patch.dict(os.environ, {"REQUIRE_SMS_OTP": "0"})
        config.start()
        self.addCleanup(config.stop)
        kb._customer_requests.clear()
        with kb._lock:
            store = kb._load()
            store["members"] = [{
                "id": "tx-customer", "name": "Test customer", "relation": "",
                "speaker_id": "test-tx", "samples": 1, "created_at": 0, "simulated": True,
            }]
            kb._save(store)
        self.client = TestClient(kb.app)
        self.details = {"amount_minor": 480000, "merchant": "Demo Store", "lang": "kn"}

    def create(self):
        response = self.client.post("/api/transactions", json={**self.details, "member_id": "tx-customer"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def device(tx):
        return "/api/confirm/device/" + tx["device_url"].split("/")[-1]

    def answer(self, device, transcript, **fields):
        return self.client.post(device + "/approve", files={"file": ("reply.wav", DEMO_AUDIO)},
                                data={"transcript": transcript, **fields})

    def status(self, tx):
        return self.client.get("/api/transactions/" + tx["id"]).json()

    def test_approval_is_bound_to_exact_amount_and_version(self):
        tx = self.create()
        self.assertEqual(tx["status"], "held")
        url = self.device(tx)
        device = self.client.get(url).json()
        self.assertEqual(device["transaction"]["amount_minor"], 480000)
        response = self.answer(url, device["phrase"], amount_minor=1)
        self.assertEqual(response.json()["status"], "confirmed")
        approval = self.status(tx)
        self.assertEqual(approval["status"], "approved")
        self.assertEqual(approval["amount_minor"], 480000)
        self.assertEqual(self.answer(url, device["phrase"]).status_code, 409)
        replacement = self.client.put("/api/transactions/" + tx["id"],
                                      json={**self.details, "amount_minor": 580000, "revision": 1}).json()
        self.assertEqual(replacement["revision"], 2)
        self.assertEqual(replacement["status"], "held")
        self.assertEqual(self.client.get(url).json()["status"], "superseded")
        self.assertEqual(self.answer(url, device["phrase"]).status_code, 409)
        self.assertEqual(self.client.get(self.device(replacement)).json()["transaction"]["amount_minor"], 580000)
        self.assertEqual(self.client.put("/api/transactions/" + tx["id"],
                                         json={**self.details, "revision": 1}).status_code, 409)

    def test_all_three_exact_words_are_required_before_provider_call(self):
        tx = self.create()
        url = self.device(tx)
        words = self.client.get(url).json()["phrase"].split()
        with patch.object(kb.client, "verify", new_callable=AsyncMock) as verify:
            out = self.answer(url, " ".join(words[:2])).json()
            self.assertEqual(out["status"], "failed")
            verify.assert_not_called()
        self.assertEqual(self.status(tx)["status"], "held")

    def test_wrong_speaker_fake_voice_and_missing_result_remain_held(self):
        for result in (
            {"result": {"decision": "no_match"}, "deepfake": {"verdict": "real"}},
            {"result": {"decision": "match"}, "deepfake": {"verdict": "fake"}},
            {"result": {"decision": "match"}, "deepfake": {"error": {"code": "insufficient_speech"}}},
        ):
            with self.subTest(result=result), patch.object(kb.client, "verify", new=AsyncMock(return_value=result)):
                tx = self.create()
                url = self.device(tx)
                self.assertEqual(self.answer(url, self.client.get(url).json()["phrase"]).json()["status"], "failed")
                self.assertEqual(self.status(tx)["status"], "held")

    def test_denied_and_expired_tokens_cannot_approve(self):
        denied = self.create()
        url = self.device(denied)
        self.assertEqual(self.client.post(url + "/deny").json()["status"], "denied")
        self.assertEqual(self.status(denied)["status"], "denied")
        self.assertEqual(self.answer(url, "").status_code, 409)

        expired = self.create()
        current = transactions.get(expired["id"])
        confirm.by_id(current["confirmation_id"])["expires_at"] = time.time() - 1
        self.assertEqual(self.status(expired)["verification"], "expired")
        self.assertEqual(self.status(expired)["status"], "held")
        self.assertEqual(self.answer(self.device(expired), "").status_code, 409)

    def test_change_during_provider_check_cannot_release_new_amount(self):
        tx = self.create()
        url = self.device(tx)
        phrase = self.client.get(url).json()["phrase"]

        async def run():
            started, finish = asyncio.Event(), asyncio.Event()

            async def verify(*_):
                started.set()
                await finish.wait()
                return {"result": {"decision": "match"}, "deepfake": {"verdict": "real"}}

            transport = httpx.ASGITransport(app=kb.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                with patch.object(kb.client, "verify", new=verify):
                    approval = asyncio.create_task(client.post(url + "/approve",
                        files={"file": ("reply.wav", DEMO_AUDIO)}, data={"transcript": phrase}))
                    await asyncio.wait_for(started.wait(), 2)
                    try:
                        changed = await client.put("/api/transactions/" + tx["id"],
                                                   json={**self.details, "amount_minor": 590000, "revision": 1})
                    finally:
                        finish.set()
                    self.assertEqual(changed.json()["status"], "held")
                    self.assertEqual((await approval).status_code, 409)

        asyncio.run(run())
        self.assertEqual(self.status(tx)["status"], "held")
