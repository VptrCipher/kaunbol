"""Provider adapters and the public customer address for transaction confirmation."""
from __future__ import annotations

import asyncio
import base64
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

import httpx

LOCALES = {"en": "en-IN", "kn": "kn-IN", "hi": "hi-IN"}
GOOGLE_HOST = "europe-west4-speech.googleapis.com"


class ProviderError(Exception):
    pass


def email_address(value: str) -> str:
    value = value.strip()
    return value if re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value) else ""


def phone_number(value: str) -> str:
    number = re.sub(r"[\s()-]", "", value)
    return number if re.fullmatch(r"\+[1-9][0-9]{7,14}", number) else ""


def otp_enabled() -> bool:
    return os.getenv("REQUIRE_SMS_OTP", "").lower() in {"1", "yes", "true"}


def verify_ready() -> bool:
    return bool(re.fullmatch(r"VA[0-9a-fA-F]{32}", os.getenv("TWILIO_VERIFY_SERVICE_SID", ""))
                and re.fullmatch(r"AC[0-9a-fA-F]{32}", os.getenv("TWILIO_ACCOUNT_SID", ""))
                and os.getenv("TWILIO_AUTH_TOKEN"))


def email_ready() -> bool:
    return bool(confirmation_base() and os.getenv("SMTP_HOST") and os.getenv("SMTP_USERNAME")
                and os.getenv("SMTP_PASSWORD") and email_address(os.getenv("SMTP_FROM_EMAIL", ""))
                and os.getenv("SMTP_PORT", "465") in {"465", "587"})


def _smtp_send(recipient: str, link: str) -> None:
    context = ssl.create_default_context()
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465"))
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM_EMAIL"]
    message["To"] = recipient
    message["Subject"] = "Kaun Bol demo payment confirmation"
    message.set_content("Review the pending demo payment on its confirmation page.\n\n" + link +
                        "\n\nEnter the SMS code only on that page. Ignore this email if you did not expect it.")
    smtp = (smtplib.SMTP_SSL(host, port, timeout=10, context=context) if port == 465
            else smtplib.SMTP(host, port, timeout=10))
    with smtp:
        if port == 587:
            smtp.starttls(context=context)
        smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


async def email_link(recipient: str, link: str) -> None:
    if not email_ready() or not email_address(recipient) or not link.startswith(confirmation_base() + "/confirm/"):
        raise ProviderError("email_unavailable")
    try:
        await asyncio.to_thread(_smtp_send, recipient, link)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        raise ProviderError("email_unavailable") from exc


async def _verify_request(path: str, data: dict) -> dict:
    if not verify_ready():
        raise ProviderError("otp_unavailable")
    service = os.environ["TWILIO_VERIFY_SERVICE_SID"]
    auth = (os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            response = await http.post(f"https://verify.twilio.com/v2/Services/{service}/{path}", auth=auth, data=data)
            if path == "VerificationCheck" and response.status_code == 404:
                raise ProviderError("otp_expired")
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("otp_unavailable") from exc


async def start_otp(phone: str) -> str:
    result = await _verify_request("Verifications", {"To": phone, "Channel": "sms"})
    sid = result.get("sid", "")
    if result.get("status") != "pending" or not re.fullmatch(r"VE[0-9a-fA-F]{32}", sid):
        raise ProviderError("otp_unavailable")
    return sid


async def check_otp(sid: str, code: str) -> bool:
    result = await _verify_request("VerificationCheck", {"VerificationSid": sid, "Code": code})
    return result.get("status") == "approved" and result.get("sid") == sid


def confirmation_base() -> str:
    root = os.getenv("CONFIRM_BASE_URL", "").strip().rstrip("/")
    if not root:
        return ""
    parsed = urlsplit(root)
    loopback = (os.getenv("ALLOW_LOOPBACK_CONFIRM") == "1" and parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    if ((parsed.scheme != "https" and not loopback) or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("CONFIRM_BASE_URL must be an HTTPS origin or an explicitly enabled loopback origin")
    return root


def device_url(token: str) -> str:
    return f"{confirmation_base()}/confirm/{token}"


def _google_token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    transport = Request()
    credentials.refresh(lambda *a, **kw: transport(*a, **{**kw, "timeout": 10}))
    return credentials.token


async def transcribe(audio: bytes, lang: str) -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    if not re.fullmatch(r"[a-z0-9-]{1,63}", project):
        raise ProviderError("speech_unconfigured")
    endpoint = f"https://{GOOGLE_HOST}/v2/projects/{project}/locations/europe-west4/recognizers/_:recognize"
    try:
        bearer = await asyncio.wait_for(asyncio.to_thread(_google_token), timeout=15)
        async with httpx.AsyncClient(timeout=18) as http:
            response = await http.post(endpoint, headers={"Authorization": f"Bearer {bearer}"}, json={
                "config": {"autoDecodingConfig": {}, "model": "chirp_2", "languageCodes": [LOCALES[lang]]},
                "content": base64.b64encode(audio).decode("ascii"),
            })
            response.raise_for_status()
            results = response.json().get("results", [])
            return " ".join(row["alternatives"][0]["transcript"] for row in results if row.get("alternatives"))
    except Exception as exc:
        raise ProviderError("speech_unavailable") from exc


def twilio_ready() -> bool:
    return bool(confirmation_base().startswith("https://")
                and re.fullmatch(r"AC[0-9a-fA-F]{32}", os.getenv("TWILIO_ACCOUNT_SID", ""))
                and os.getenv("TWILIO_AUTH_TOKEN")
                and (os.getenv("TWILIO_FROM_NUMBER") or os.getenv("TWILIO_MESSAGING_SERVICE_SID")))


async def send_link(recipient: str, url: str) -> str:
    if not twilio_ready() or not url.startswith(confirmation_base() + "/confirm/"):
        raise ProviderError("sms_unconfigured")
    number = re.sub(r"[\s()-]", "", recipient)
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
        raise ProviderError("sms_number")
    sid, secret = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    payload = {"To": number, "Body": f"Kaun Bol bank demo. Review your pending payment: {url}"}
    service = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
    if service:
        payload["MessagingServiceSid"] = service
    else:
        payload["From"] = os.environ["TWILIO_FROM_NUMBER"]
    try:
        async with httpx.AsyncClient(timeout=12) as http:
            response = await http.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                                       auth=(sid, secret), data=payload)
            response.raise_for_status()
            message = response.json()
            if message.get("status") in ("failed", "undelivered", "canceled"):
                raise ProviderError("sms_rejected")
            if not message.get("sid"):
                raise ProviderError("sms_unknown")
            return message["sid"]
    except httpx.HTTPStatusError as exc:
        raise ProviderError("sms_rejected" if exc.response.status_code < 500 else "sms_unknown") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise ProviderError("sms_unknown") from exc
