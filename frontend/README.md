# Frontend

[Project overview](../README.md) · [Architecture](../docs/ARCHITECTURE.md)

The UI is plain HTML, CSS, and JavaScript, served by FastAPI. Run the backend and open `http://127.0.0.1:8003`; opening HTML as a local file will not provide the API or audio flows.

## Files

- `index.html` and `app.js`: agent console, customer enrollment, voice screening, live guard, registry and transaction review.
- `confirm.html`: customer challenge, transaction details, approval and denial.
- `verify.html`: outbound caller verification surface.
- `audio.js`: recording and audio input helpers.
- `i18n.js`: English, Hindi and Kannada UI text.
- `styles.css`: shared visual design, interaction states and motion.
- `sw.js`: app-shell caching and shared-audio handoff; API requests are never cached.

## Motion design

Panels enter with a short fade and vertical movement, cards appear in a restrained stagger, and buttons respond to hover and press. Result cards reveal once when inserted. Existing live/recording indicators continue to represent their actual runtime state. Motion never changes transaction state or creates a verification result.

`prefers-reduced-motion: reduce` removes animations and transitions, including recording pulses, incoming-call movement, and live status rings. Text and color remain available to communicate state. Hover movement is limited to devices with a fine pointer.

## Browser checks

Check tab changes, keyboard focus, disabled controls, transaction details, 375 px layouts, and reduced-motion mode. Microphone access needs localhost or HTTPS. System-audio capture and speech recognition depend on browser and device support; use the in-app input test before a rehearsal. Native speakers should review Hindi and Kannada copy and challenges before a live demonstration.
