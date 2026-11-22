/* 서비스워커: 설치형 웹앱(PWA)용.
 * - 화면 파일만 캐시하고, "네트워크 우선 → 실패하면 캐시" 전략을 쓴다.
 *   (항상 최신 파일을 먼저 받으므로 개발 중 옛 JS가 남는 문제가 없고, 오프라인일 때만 캐시를 쓴다)
 * - /api/* 는 절대 캐시하지 않는다. (판정 결과가 옛것으로 보이면 안 됨)
 * - 외부 도메인(jsQR CDN, 구글 폰트)은 가로채지 않는다.
 * 화면 파일 목록을 바꾸면 VERSION 을 올린다. 활성화될 때 옛 버전 캐시는 지워진다.
 */
const VERSION = "v4";
const CACHE = `qr-shell-${VERSION}`;
const SHELL = [
  "/",
  "/static/index.html",
  "/static/app.js",
  "/static/style.css",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("qr-shell-") && k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 가로채지 않는 요청: 외부 도메인, GET 이 아닌 요청, /api/*
  // (respondWith 를 부르지 않으면 브라우저가 평소처럼 네트워크로 보낸다)
  if (url.origin !== self.location.origin) return;
  if (req.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) return;
  if (!SHELL.includes(url.pathname)) return;

  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req, { ignoreSearch: true }))
  );
});
