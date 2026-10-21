// SPDX-License-Identifier: AGPL-3.0-only
// shortcut: CACHE is bumped by hand when a precached file changes, upgrade: derive it from a build hash.
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

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
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
  // Only precached shell files are answered from cache; everything else goes to the network uncached.
  if (url.pathname.startsWith("/api/") || url.pathname === "/share") return;
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((hit) => hit ?? fetch(event.request)),
  );
});
