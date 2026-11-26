// Kaun Bol Raha Hai? service worker.
//  1. Makes the app installable and keeps the shell loading when the network drops.
//  2. Receives a voice note shared from WhatsApp ("Share > Kaun Bol") and hands it to the Check tab.
// API calls are never cached: a stale verdict would be worse than no verdict.
const SHELL = "kb-shell-bank-motion-v5"; // v5: interaction motion and reduced-motion support
const SHARE = "kb-share";
const ASSETS = ["/", "/static/styles.css", "/static/app.js", "/static/audio.js", "/static/i18n.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS.map((path) => new Request(path, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("kb-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (req.method === "POST" && url.pathname === "/share-target") {
    e.respondWith((async () => {
      try {
        const form = await req.formData();
        const file = form.get("audio");
        if (!file || !file.size) return Response.redirect("/?shared=0", 303);
        const cache = await caches.open(SHARE);
        await cache.put("/shared-clip", new Response(file, {
          headers: { "Content-Type": file.type || "audio/ogg", "X-Name": encodeURIComponent(file.name || "voice-note") },
        }));
        return Response.redirect("/?shared=1", 303);
      } catch {
        return Response.redirect("/?shared=0", 303);
      }
    })());
    return;
  }

  // Network first for the app shell, fall back to cache when offline. Everything else goes straight through.
  const isShell = req.method === "GET" && (url.pathname === "/" || ASSETS.includes(url.pathname));
  if (isShell) {
    e.respondWith(fetch(req, { cache: "no-cache", signal: AbortSignal.timeout(3500) })
      .catch(async () => (await caches.match(url.pathname, { cacheName: SHELL })) || Response.error()));
  }
});
