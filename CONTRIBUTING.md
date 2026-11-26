# Contributing

1. Start in demo mode using the [README](README.md).
2. Keep changes focused and describe the visible behavior they change.
3. Run `python -m unittest discover -s tests` from `backend/` for backend changes. Check desktop/mobile layouts and reduced-motion settings for UI changes.
4. Include reproduction steps and validation in your pull request. Identify mocked behavior and real provider/device checks separately.

Never commit `.env`, credentials, enrolled customer metadata, phone histories, databases, or voice recordings. Use consented inputs stored outside source control. The benchmark source recordings are intentionally absent; a clean checkout can read saved results but cannot reproduce live metrics without additional private inputs.

See [architecture](docs/ARCHITECTURE.md) before changing transaction revisions, provider checks, session storage, or customer route exposure.
