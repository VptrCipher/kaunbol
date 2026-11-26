# Bank edition validation

- The main page always uses bank terminology and opens without a profile selector. The Customers, voice screening, live call guard, outbound verification, and evidence workflows remain accessible.
- Transactions start held. Confirmations bind to the customer, exact amount, merchant, and a single revision. Editing details replaces the link and discards its approval for the new version.
- The agent sees the voice, speaker and phrase outcomes on the same screen as the transaction status.
- A transaction stays held when the provider gives no explicit real-voice result, the phrase is missing or wrong, or the link expires. A customer denial is shown separately.
- The browser customer page is available in English, Hindi, and Kannada, with a read-aloud control on supported devices. Hindi and Kannada copy and the recognition behavior on an actual phone need a rehearsal with native speakers.
- The application runs with a single worker. Active transaction and call state is in memory.

## Checks run on 26 November 2026

- Backend suite passed 70 tests. These include replay normalization, exact transaction binding, denying an expired request, explicit failure on a missing deepfake verdict, and changing an amount while a provider call is running.
- Chrome completed the transaction flow across two browser tabs. It opened the Kannada challenge, recorded a fixture, approved a simulated transaction, rejected the old Kannada link after the amount was edited, and showed the Hindi denial on the desktop.
- The Chrome test used fixture media and speech recognition. A real phone call, Android or iOS microphone, real transcription, and live DetectifAI require device and account tests.
- The PC audio update passed the backend suite and a Chrome fixture test that confirmed explicit audio-input selection, a clear result when a session is silent, and audio arriving from a browser share. The test verified that stopping the guard releases the share track. A real Phone Link output still needs a device test.

## Demo priorities

Show the handset ring and the exact amount on the confirmation page. Then show a successful verification, edit just the amount on the desktop, and open the old link to show it has been replaced. For a separate negative case, show the second request being denied on the customer screen. If voice recognition on the phone is unreliable, use the demo laptop to test the confirmation page and show the failed check honestly.
