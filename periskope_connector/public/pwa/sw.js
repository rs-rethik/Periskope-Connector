// The WhatsApp inbox's service worker, served at /whatsapp/sw.js (pwa.py) so it controls /whatsapp/.
// It makes the app installable and quick to start: the built files (hashed, so they never change) are
// kept after the first use, pages always come from the server, and an offline page shows when there
// is no network. Chats and messages are never cached: they come live from the API.
//
// A new version waits until the open tabs are closed (or the app asks it to take over), and the
// previous version's files are kept one release longer: a tab still running the old code can load
// its remaining pages from them after a deploy removed them from the server.

const VERSION = "__VERSION__";
const CACHE = `whatsapp-inbox-${VERSION}`;
const OFFLINE = "/whatsapp/__offline";
const KEEP = ["/assets/periskope_connector/inbox/", "/assets/periskope_connector/pwa/"];

const OFFLINE_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline · __APP_NAME__</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,sans-serif;
background:#eef3f0;color:#17302b;text-align:center;padding:24px;box-sizing:border-box}
@media (prefers-color-scheme:dark){body{background:#0f1817;color:#e4eeeb}p{color:#9bb0aa}}
img{width:72px;height:72px;border-radius:18px}h1{font-size:22px;margin:16px 0 6px}
p{color:#566a64;margin:0 0 20px;max-width:320px}
button{border:0;border-radius:999px;background:#0d5c55;color:#fff;font-size:15px;padding:10px 22px}
</style></head><body><main>
<img src="/assets/periskope_connector/pwa/icon-192.png" alt="">
<h1>You're offline</h1><p>The inbox needs the internet to show chats. It will be back as soon as you're online.</p>
<button onclick="location.reload()">Try again</button></main></body></html>`;

self.addEventListener("install", (event) => {
	event.waitUntil(
		caches.open(CACHE).then((cache) =>
			Promise.all([
				cache.put(
					OFFLINE,
					new Response(OFFLINE_PAGE, {
						headers: { "Content-Type": "text/html; charset=utf-8" },
					})
				),
				cache.add("/assets/periskope_connector/pwa/icon-192.png").catch(() => {}),
			])
		)
	);
});
self.addEventListener("message", (event) => {
	if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("activate", (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) => {
				// keys come oldest first: keep this version's cache and the one before it
				const older = keys.filter((k) => k.startsWith("whatsapp-inbox-") && k !== CACHE);
				return Promise.all(older.slice(0, -1).map((k) => caches.delete(k)));
			})
			.then(() => self.clients.claim())
	);
});

self.addEventListener("fetch", (event) => {
	const request = event.request;
	if (request.method !== "GET") return;
	const url = new URL(request.url);
	if (url.origin !== self.location.origin) return;

	if (request.mode === "navigate") {
		event.respondWith(fetch(request).catch(() => caches.match(OFFLINE)));
		return;
	}
	if (!KEEP.some((prefix) => url.pathname.startsWith(prefix))) return;
	event.respondWith(
		caches.open(CACHE).then(async (cache) => {
			const hit = await caches.match(request); // this version's files or the previous one's
			if (hit) return hit;
			const response = await fetch(request);
			if (response.ok) cache.put(request, response.clone());
			return response;
		})
	);
});
