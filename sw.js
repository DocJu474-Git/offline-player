const CACHE = 'offline-player-v4';
const ASSETS = ['./', './index.html', './manifest.json', './icon.svg', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => c.addAll(ASSETS.map(u => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  const isPage = req.mode === 'navigate' || /\/(index\.html)?(\?.*)?$/.test(req.url);
  if (isPage) {
    // Netz zuerst (damit Updates sofort ankommen), sonst Cache
    e.respondWith(
      Promise.race([
        fetch(req, { cache: 'no-cache' }).then(res => { if (res.ok) caches.open(CACHE).then(c => c.put('./index.html', res.clone())); return res; }),
        new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 4000)),
      ]).catch(() => caches.match('./index.html'))
    );
    return;
  }
  e.respondWith(
    caches.match(req, { ignoreSearch: true }).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }))
  );
});
