# Kaun Bol bank demo

## Run on Windows

From the extracted `kaunbol` folder, run these in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
$env:DEMO_MODE = "1"
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Open `http://127.0.0.1:8000`. Add a consenting customer in **Customers** and save their
registered phone with its country code. Open **Transaction review**, enter a test amount and
payee, and select **Place on hold**. Use **Open customer request** for a test on the same PC.
QR scanning on a separate phone requires a reachable HTTPS address.

## Live providers and HTTPS

| Setting | What to supply |
| --- | --- |
| `DETECTIF_API_KEY` | A DetectifAI key with access to Aletheia, Eidos enrollment and verification, and live streaming for the call guard. |
| `GOOGLE_CLOUD_PROJECT` | The Google Cloud project ID. Enable Speech-to-Text and configure Application Default Credentials with Speech Client access. Local development can use `gcloud auth application-default login`. The app uses Chirp 2 in `europe-west4`. |
| `CONFIRM_BASE_URL` | The HTTPS origin of the customer page, supplied through the local environment or deployment settings. |
| `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` | Optional Twilio credentials. The app also needs `TWILIO_FROM_NUMBER` or `TWILIO_MESSAGING_SERVICE_SID`. |
| Customer profile | Save the handset's registered number in international format. On trial, verify the handset in Twilio before attempting SMS. |

Set `DEMO_MODE=0` in the runtime environment for live calls. Restart the server after edits.
Keep credentials in the local environment or the deployment's secret store. Twilio handles
link delivery. The phone records the challenge in the browser and sends audio to Kaun Bol.
Twilio's trial can reject custom SMS bodies. Use the QR flow for the rehearsal and only count
SMS as delivered when the handset receives the actual link.

Use a separate hostname for the customer site and preserve its Host header in the HTTPS proxy.
Keep the agent backend bound to loopback or a private network and set `AGENT_PIN`. Expose only
`/confirm/*`, `/api/confirm/device/*`, and the three customer assets
`/static/styles.css`, `/static/audio.js`, and `/static/i18n.js` on the public proxy.
The app also restricts the configured customer host to those routes.
Run a single worker so active links and call sessions use the same state. Disable proxy access
logs for token-bearing paths. Confirm that the public homepage and agent endpoints return 404.

## Rehearse

For email plus SMS codes, set `REQUIRE_SMS_OTP=1` and `TWILIO_VERIFY_SERVICE_SID` for
your project's Twilio Verify service. The Verify flow uses your Account SID and Auth
Token. Set up your email provider's `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`
and `SMTP_FROM_EMAIL`. Use port 465 for TLS or 587 for STARTTLS. Use the SMTP password
or app password provided for this mailbox. Set `CONFIRM_BASE_URL` to the customer HTTPS
origin. Under **Customers**, save the test customer's registered email and phone number.
Under **Transaction review**, place a payment on hold and click **Email confirmation link**.
The agent can use **Send SMS verification code** separately. Both shortcuts are beside
**Review a payment** in the live panel. If the agent has already requested the code, the customer
can open the emailed page and enter it. Otherwise the customer can request it from that page.
The customer then completes the voice challenge and approves.

For a rehearsal with the browser on the same PC, you can set
`CONFIRM_BASE_URL=http://127.0.0.1:8009` and `ALLOW_LOOPBACK_CONFIRM=1` and run
Uvicorn on port 8009. Open the email link on that PC and read the code from your phone.
This rehearsal does not make the browser page accessible from the phone.

The emailed link expires after five minutes. The app prevents overlapping pending codes for the same
recipient. If a request is replaced while its code is pending, use a different test customer or wait
for that code to expire. Keep the Verify service dedicated to this app. If running with simulated
voice checks, identify the voice results as simulated even when email and OTP providers are live.

1. Hold a payment and open its customer page by QR. Select Kannada or Hindi and speak the phrase;
   allow at least five seconds for the recording. The live server transcribes the same recording
   it checks with Eidos and Aletheia. Demo mode uses the browser transcript and labels both
   DetectifAI results simulated. Use a browser with speech recognition for demo mode.
2. Change the amount and reopen the previous link. Only the new amount can be approved.
3. On a separate case, report the phone lost. The report persists and future requests for that
   customer stay held. Resolve the report through manual review. For repeated rehearsals, use a
   separate demo customer because there is no self-service reset.

The transaction state represents the demo's decision. It is not connected to a payment rail.
