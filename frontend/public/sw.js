// App-shell service worker.
//
// The old version answered every request with ``caches.match(request) ||
// fetch(request)``: navigation responses were served from a cache that was
// filled once and never refreshed (the cache name never changed and there was
// no activate handler), so a browser could keep running a stale shell no
// matter how often the app was updated.  Now navigations are network-first
// (the cache is only an offline fallback), hashed build assets stay
// cache-first, and API calls are never intercepted.
const CACHE = "novelhub-shell-v2";
const SHELL = ["/", "/index.html", "/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const request = e.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Never touch the API: chapters are long and must always be current.
  if (url.pathname.startsWith("/api/")) return;

  const isNavigation =
    request.mode === "navigate" ||
    (request.headers.get("accept") || "").includes("text/html");
  if (isNavigation) {
    e.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put("/index.html", copy)).catch(() => undefined);
          return response;
        })
        .catch(() => caches.match("/index.html").then((cached) => cached || caches.match("/"))),
    );
    return;
  }

  // Hashed build output and other static files: cache first, refresh in the
  // background so a new deploy still replaces them.
  if (url.pathname.startsWith("/assets/")) {
    e.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => undefined);
            return response;
          }),
      ),
    );
  }
});
