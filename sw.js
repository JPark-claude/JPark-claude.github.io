/* 마켓 데스크 서비스 워커
   - 앱으로 설치할 수 있게 하고, 인터넷이 끊겼을 때 마지막으로 본 화면과 데이터를 보여준다.
   - 페이지·데이터(json)는 항상 네트워크를 먼저 쓰고, 실패할 때만 저장본을 쓴다(시세가 묵지 않도록).
   - 다른 사이트(TradingView 위젯 등) 요청은 건드리지 않는다. */
const CACHE = "market-desk-v1";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          const key = url.pathname.endsWith(".json") ? new Request(url.origin + url.pathname) : req;
          caches.open(CACHE).then((c) => c.put(key, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(url.pathname.endsWith(".json") ? new Request(url.origin + url.pathname) : req).then(
          (hit) => hit || caches.match("/index.html")
        )
      )
  );
});
