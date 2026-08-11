/* Offline shell.

   Network-first, cache-as-fallback. A house app is useless if it shows you
   yesterday's chore list as though it were today's, so the network always wins
   when it's available; the cache exists only so the app opens at all on a dead
   signal in a basement laundry room. */

// Bump CACHE whenever the shell changes -- `activate` deletes every other
// cache, which is what evicts a stale app.js from an installed home-screen app.
// Keep the ?v= numbers here in step with the ones in index.html.
const CACHE = 'casita-v2';
const SHELL = [
  '/', '/index.html', '/styles.css?v=2', '/app.js?v=2',
  '/manifest.webmanifest', '/icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // API responses are never cached: stale chore state is worse than no state.
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match('/index.html')))
  );
});
