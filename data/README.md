# data/ (not included in the zip on purpose)

Left out because they hold real voices, phone numbers and call history:
- `family.json` (copy `family.example.json` to `family.json`, then enrol your own consented speakers)
- `*.db` (created automatically on first run)
- `benchmark/*.wav` (add your own consented clips; `manifest.json` lists the 5 original cases)
- `demo_clips/*.wav` (sample clips for the "Try a sample" buttons; served only through the PIN-guarded `/api/demo-clip/<name>`)

Never commit these.
