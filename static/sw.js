'use strict';
const CACHE = 'careerpilot-public-v1';
const OFFLINE = '/static/offline.html';
const PUBLIC_ASSETS = [OFFLINE, '/static/style.css',
  '/static/icons/apple-touch-icon.png', '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PUBLIC_ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('careerpilot-public-') && key !== CACHE)
      .map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  // Never queue writes or cache CVs, jobs, credentials, downloads or API responses.
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() =>
      caches.open(CACHE).then(cache => cache.match(OFFLINE))
        .then(response => response || new Response('Reconnect to open Careerpilot.',
          {status: 503, headers: {'Content-Type': 'text/plain'}}))
    ));
  } else if (PUBLIC_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.open(CACHE).then(async cache =>
      (await cache.match(url.pathname)) || fetch(request)));
  }
});
