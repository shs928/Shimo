/* 拾墨 Service Worker：只缓存静态资源，绝不缓存 /api 与未保存正文。 */
const CACHE = 'shimo-static-v1'
const STATIC = ['/', '/index.html', '/manifest.webmanifest', '/icon.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(STATIC))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  // 只缓存同源静态资源（构建产物 /assets/ 带内容哈希，可安全缓存）
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return // API 一律网络
  if (event.request.method !== 'GET') return
  if (url.pathname.startsWith('/assets/') || url.pathname === '/') {
    event.respondWith(
      caches.match(event.request).then((hit) => hit || fetch(event.request).then((resp) => {
        const copy = resp.clone()
        caches.open(CACHE).then((cache) => cache.put(event.request, copy))
        return resp
      })),
    )
  }
})
