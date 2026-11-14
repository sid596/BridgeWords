// BridgeWords service worker: the page, its files and the board load without internet.
// Network first (so updates show up), cache as the fallback. API POSTs are never cached.
const CACHE = "bridgewords-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/manifest.webmanifest",
               "/static/icons/icon.svg", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                       // speech, suggestions: always live
  const url = new URL(req.url);
  const cacheable = url.origin === location.origin
    ? (url.pathname === "/" || url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest"
       || url.pathname === "/api/symbols")
    : url.hostname.endsWith("fonts.googleapis.com") || url.hostname.endsWith("fonts.gstatic.com");
  if (!cacheable) return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req))
  );
});
