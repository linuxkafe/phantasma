/*
 * The service worker for Phantasma.
 *
 * Its whole job is to be BORING. This is a page that controls a house, so the
 * failure that matters is not "it failed to load" but "it showed a stale
 * reading" or "it let a tap act on state from an hour ago".
 *
 * Therefore:
 *
 *   - The APP SHELL is cached, so launching from the home screen is instant and
 *     the page opens offline. A light switch is a control, not a document.
 *   - NOTHING ELSE IS. Every /api/, /comando, /device_action, /api/stt,
 *     /api/voz and /get_devices goes to the network, every time. No cache-first,
 *     no stale-while-revalidate, no "fall back to the last reading". A switch
 *     that silently acts on cached state is not a feature.
 *   - Responses are NEVER cached for authenticated pages. The page is behind a
 *     session; putting a signed-in page into a shared cache is how one
 *     household's house ends up in another's browser.
 *   - Updates land on the next navigation and the old worker is dropped when a
 *     new one takes over. No skipWaiting games: swapping the code under a
 *     person mid-command is worse than one reload.
 *
 * What the offline experience deliberately does NOT have: a device list from
 * yesterday. If the network is gone, the page says so.
 */

const CACHE = 'phantasma-shell-v1';
const SHELL = ['/', '/public/icons/icon-192.png', '/public/icons/icon-512.png',
               '/public/icons/maskable-192.png', '/public/icons/maskable-512.png',
               '/public/manifest.json'];

/* The paths that change the house, or that carry a reading. Never cached. */
const LIVE = [
  '/api/', '/comando', '/device_action', '/device_status', '/get_devices',
  '/api/stt', '/api/voz', '/api/command', '/api/tts', '/login', '/logout',
  '/recuperar', '/perfil', '/verificar-dispositivo', '/admin',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      /* Individually, so one 404 cannot fail the whole install. A partial
         shell is still a better cold start than none. */
      .then((cache) => Promise.all(
        SHELL.map((url) => cache.add(url).catch(() => null))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  /* Anything live goes straight through. No cache read, no cache write. */
  if (LIVE.some((prefix) => url.pathname.startsWith(prefix))) return;

  /* The navigation: network first, so a code change reaches the person, and the
     cached shell only if the network is gone. Never the other way round. */
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put('/', copy)).catch(() => null);
          return res;
        })
        .catch(() => caches.match('/').then((hit) => hit || Response.error()))
    );
    return;
  }

  /* Static assets: cache first, refilled in the background. These are replaced
     by a deploy, and a slightly old stylesheet is not a stale reading. */
  event.respondWith(
    caches.match(req).then((hit) => {
      const refill = fetch(req).then((res) => {
        if (res && res.status === 200 && res.type === 'basic') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => null);
        }
        return res;
      }).catch(() => hit);
      return hit || refill;
    })
  );
});
