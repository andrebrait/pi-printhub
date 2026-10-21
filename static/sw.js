// SPDX-License-Identifier: AGPL-3.0-only
// shortcut: vendor and icon files stay cache-first until CACHE is bumped by hand, upgrade: version their URLs.
const CACHE = "printhub-v1";

const PRECACHE = [
  "/",
  "/index.html",
  "/login.html",
  "/print.html",
  "/scan.html",
  "/manifest.webmanifest",
  "/css/app.css",
  "/js/api.js",
  "/js/crop.js",
  "/js/layout.js",
  "/js/print.js",
  "/js/scan.js",
  "/js/sw-register.js",
  "/vendor/cropper/cropper.min.css",
  "/vendor/cropper/cropper.min.js",
  "/vendor/sortable/Sortable.min.js",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-512.png",
];

// Vendor and icon files keep their names and rarely change, so they are served from cache first.
const CACHE_FIRST = ["/vendor/", "/icons/"];

self.addEventListener("install", (event) => {
  // "reload" skips the HTTP cache so the precache holds what the server serves now.
  const requests = PRECACHE.map((path) => new Request(path, { cache: "reload" }));
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(requests)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  // API and share requests are never answered from cache.
  if (url.pathname.startsWith("/api/") || url.pathname === "/share") return;
  const cacheFirst = CACHE_FIRST.some((prefix) => url.pathname.startsWith(prefix));
  event.respondWith(cacheFirst ? cacheOrNetwork(event.request) : networkOrCache(event.request, url));
});

async function cacheOrNetwork(request) {
  return (await caches.match(request, { ignoreSearch: true })) ?? fetch(request);
}

// A fresh response wins and refreshes the cache; the cached copy answers only when the network fails.
async function networkOrCache(request, url) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE);
      await cache.put(url.pathname, response.clone());
    }
    return response;
  } catch {
    return (await caches.match(request, { ignoreSearch: true })) ?? Response.error();
  }
}
