import os
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import test_transactions
import app as kb


class StepUpTests(unittest.TestCase):
    def setUp(self):
        fixture = test_transactions.TransactionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.agent = fixture.client
        self.details = fixture.details
        self.member = "stepup-" + self._testMethodName
        with kb._lock:
            store = kb._load()
            store["members"][0].update(id=self.member, phones=["+919876543210"])
            kb._save(store)

    def issue(self):
        response = self.agent.post("/api/transactions", json={**self.details, "member_id": self.member})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def device(tx):
        return "/api" + urlsplit(tx["device_url"]).path.replace("/confirm/", "/confirm/device/")

    def approve(self, device, transcript):
        return self.agent.post(device + "/approve", data={"transcript": transcript},
                               files={"file": ("reply.wav", b"audio fixture")})

    def test_language_switch_then_exact_confirmation(self):
        tx = self.issue()
        url = self.device(tx)
        original = self.agent.get(url).json()
        self.assertEqual(original["lang"], "kn")
        self.assertEqual(len(original["phrase"].split()), 3)
        switched = self.agent.post(url + "/language", json={"lang": "hi"})
        self.assertEqual(switched.status_code, 200)
        device = switched.json()
        self.assertEqual(device["lang"], "hi")
        self.assertNotEqual(device["phrase"], original["phrase"])
        self.assertEqual(self.approve(url, device["phrase"]).json()["status"], "confirmed")
        self.assertEqual(self.agent.post(url + "/language", json={"lang": "en"}).status_code, 409)

    def test_mixed_model_evidence_holds_even_with_good_identity(self):
        tx = self.issue()
        url = self.device(tx)
        phrase = self.agent.get(url).json()["phrase"]
        mixed = {"result": {"decision": "match"},
                 "deepfake": {"verdict": "real", "segments": [{"verdict": "fake"}]}}
        with patch.object(kb.client, "verify", new=AsyncMock(return_value=mixed)):
            response = self.approve(url, phrase).json()
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["result"]["voice"], "fake")
        self.assertEqual(response["result"]["decision"], "match")

    def test_lost_phone_revokes_all_links_and_blocks_reissue(self):
        first, second = self.issue(), self.issue()
        link = self.device(first)
        phrase = self.agent.get(link).json()["phrase"]
        report = self.agent.post(f"/api/transactions/{second['id']}/report-device").json()
        self.assertEqual((report["status"], report["verification"], report["device_url"]), ("held", "revoked", None))
        self.assertEqual(self.agent.get(link).json(), {"status": "revoked", "lang": "kn"})
        self.assertEqual(self.approve(link, phrase).status_code, 409)
        self.assertIsNone(self.issue()["device_url"])

    def test_live_phrase_uses_audio_and_ignores_browser_text(self):
        tx = self.issue()
        url = self.device(tx)
        phrase = self.agent.get(url).json()["phrase"]
        verify = AsyncMock(return_value={"result": {"decision": "match"}, "deepfake": {"verdict": "real"}})
        speech = AsyncMock(return_value=phrase)
        with patch.object(kb.client, "demo", False), patch.object(kb.stepup, "transcribe", speech), \
                patch.object(kb.client, "verify", verify):
            answer = self.approve(url, "wrong browser transcript").json()
        speech.assert_awaited_once_with(b"audio fixture", "kn")
        verify.assert_awaited_once()
        self.assertEqual(answer["status"], "confirmed")
        self.assertFalse(answer["result"]["simulated"])

    def test_an_unavailable_live_transcriber_keeps_payment_held(self):
        tx = self.issue()
        url = self.device(tx)
        phrase = self.agent.get(url).json()["phrase"]
        verify = AsyncMock()
        with patch.object(kb.client, "demo", False), patch.object(kb.client, "verify", verify), \
                patch.object(kb.stepup, "transcribe", AsyncMock(side_effect=kb.stepup.ProviderError("speech_unavailable"))):
            answer = self.approve(url, phrase).json()
        self.assertEqual(answer["status"], "failed")
        verify.assert_not_called()

    def test_public_host_qr_and_single_sms_to_registered_number(self):
        config = {
            "CONFIRM_BASE_URL": "https://customer.test", "TWILIO_ACCOUNT_SID": "AC" + "a" * 32,
            "TWILIO_AUTH_TOKEN": "test-only", "TWILIO_FROM_NUMBER": "+15555550123",
        }
        with patch.dict(os.environ, config), patch.object(kb, "CONFIRM_HOST", "customer.test"):
            sender = AsyncMock(return_value="SM-test")
            with patch.object(kb.stepup, "send_link", sender):
                tx = self.issue()
                self.assertTrue(tx["device_url"].startswith("https://customer.test/confirm/"))
                qr = self.agent.get(f"/api/transactions/{tx['id']}/qr?revision=1")
                self.assertEqual(qr.headers["content-type"], "image/svg+xml")
                self.assertIn(b"<path", qr.content)
                self.assertEqual(qr.headers["cache-control"], "no-store")
                self.assertEqual(self.agent.get(f"/api/transactions/{tx['id']}/qr?revision=2").status_code, 409)
                path = f"/api/transactions/{tx['id']}/sms?revision=1"
                self.assertEqual(self.agent.post(path).json()["sms"], "accepted")
                self.assertEqual(self.agent.post(path).json()["sms"], "accepted")
                sender.assert_awaited_once_with("+919876543210", tx["device_url"])
            headers = {"Host": "customer.test"}
            for forbidden in ("/", "/static/app.js", "/api/status", "/docs", "/api/family"):
                self.assertEqual(self.agent.get(forbidden, headers=headers).status_code, 404)
            self.assertEqual(self.agent.get(self.device(tx), headers=headers).headers["cache-control"], "no-store")
            self.assertEqual(self.agent.get(urlsplit(tx["device_url"]).path, headers=headers).status_code, 200)

    def test_emailed_confirmation_requires_sms_code_before_voice(self):
        config = {
            "REQUIRE_SMS_OTP": "1", "CONFIRM_BASE_URL": "https://customer.test",
            "TWILIO_ACCOUNT_SID": "AC" + "a" * 32, "TWILIO_AUTH_TOKEN": "fixture",
            "TWILIO_VERIFY_SERVICE_SID": "VA" + "b" * 32, "SMTP_HOST": "smtp.test",
            "SMTP_USERNAME": "fixture", "SMTP_PASSWORD": "fixture", "SMTP_FROM_EMAIL": "bank@example.test",
        }
        member = self.agent.put(f"/api/family/{self.member}/email", json={"email": "customer@example.test"})
        self.assertEqual(member.status_code, 200)
        with patch.dict(os.environ, config), \
                patch.object(kb.stepup, "email_link", AsyncMock()) as email, \
                patch.object(kb.stepup, "start_otp", AsyncMock(return_value="VE" + "c" * 32)) as start, \
                patch.object(kb.stepup, "check_otp", AsyncMock(side_effect=[False, True])) as check:
            tx = self.issue()
            device = self.device(tx)
            self.assertEqual(self.agent.get(device).json()["phrase"], "")
            self.assertEqual(self.agent.post(device + "/otp-start").status_code, 409)
            self.assertEqual(self.approve(device, "anything").status_code, 409)
            send = f"/api/transactions/{tx['id']}/email?revision=1"
            self.assertEqual(self.agent.post(send).json()["email_delivery"], "accepted")
            self.assertEqual(self.agent.post(send).json()["email_delivery"], "accepted")
            email.assert_awaited_once_with("customer@example.test", tx["device_url"])
            self.assertEqual(self.agent.post(device + "/otp-start").json()["otp"], "sent")
            start.assert_awaited_once_with("+919876543210")
            self.assertEqual(self.agent.post(device + "/otp-check", json={"code": "111111"}).status_code, 409)
            result = self.agent.post(device + "/otp-check", json={"code": "222222"}).json()
            self.assertEqual(result["otp"], "verified")
            check.assert_awaited_with("VE" + "c" * 32, "222222")
            self.assertEqual(self.approve(device, result["phrase"]).json()["status"], "confirmed")
            revised = self.agent.put(f"/api/transactions/{tx['id']}", json={**self.details,
                                     "amount_minor": 490000, "revision": 1}).json()
            self.assertEqual(self.approve(self.device(revised), result["phrase"]).status_code, 409)
            self.assertEqual(self.agent.post(device + "/otp-check", json={"code": "222222"}).status_code, 409)

    def test_rehearsal_origin_is_explicit_and_loopback_only(self):
        with patch.dict(os.environ, {"CONFIRM_BASE_URL": "http://127.0.0.1:8009", "ALLOW_LOOPBACK_CONFIRM": "0"}):
            with self.assertRaises(ValueError):
                kb.stepup.confirmation_base()
        with patch.dict(os.environ, {"CONFIRM_BASE_URL": "http://127.0.0.1:8009", "ALLOW_LOOPBACK_CONFIRM": "1"}):
            self.assertEqual(kb.stepup.confirmation_base(), "http://127.0.0.1:8009")
            self.assertFalse(kb.stepup.twilio_ready())
        with patch.dict(os.environ, {"CONFIRM_BASE_URL": "http://customer.test", "ALLOW_LOOPBACK_CONFIRM": "1"}):
            with self.assertRaises(ValueError):
                kb.stepup.confirmation_base()

    def test_agent_can_send_sms_before_email_without_revealing_code(self):
        config = {
            "REQUIRE_SMS_OTP": "1", "CONFIRM_BASE_URL": "https://customer.test",
            "TWILIO_ACCOUNT_SID": "AC" + "a" * 32, "TWILIO_AUTH_TOKEN": "fixture",
            "TWILIO_VERIFY_SERVICE_SID": "VA" + "b" * 32, "SMTP_HOST": "",
        }
        self.agent.put(f"/api/family/{self.member}/phones", json={"phones": ["+919876543211"]}).raise_for_status()
        self.agent.put(f"/api/family/{self.member}/email", json={"email": "customer@example.test"}).raise_for_status()
        with patch.dict(os.environ, config), \
                patch.object(kb.stepup, "start_otp", AsyncMock(return_value="VE" + "c" * 32)) as start, \
                patch.object(kb.stepup, "email_link", AsyncMock()) as email:
            tx = self.issue()
            path = f"/api/transactions/{tx['id']}"
            self.assertTrue(tx["otp_available"])
            self.assertFalse(tx["email_available"])
            self.assertEqual(self.agent.post(path + "/otp?revision=2").status_code, 409)
            sent = self.agent.post(path + "/otp?revision=1").json()
            self.assertEqual(sent["otp"], "sent")
            self.assertNotIn("otp_sid", sent)
            self.assertNotIn("otp_target", sent)
            self.assertEqual(self.agent.post(path + "/otp?revision=1").json()["otp"], "sent")
            start.assert_awaited_once_with("+919876543211")
            self.assertEqual(self.agent.post(self.device(tx) + "/otp-check", json={"code": "222222"}).status_code, 409)
            with patch.dict(os.environ, {"TWILIO_VERIFY_SERVICE_SID": "", "SMTP_HOST": "smtp.test",
                                        "SMTP_USERNAME": "fixture", "SMTP_PASSWORD": "fixture",
                                        "SMTP_FROM_EMAIL": "demo@example.test"}):
                self.assertEqual(self.agent.post(path + "/email?revision=1").json()["email_delivery"], "accepted")
                email.assert_awaited_once_with("customer@example.test", tx["device_url"])
