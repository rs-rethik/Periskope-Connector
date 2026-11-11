// Notifications on this device (web push, push.py): the browser asks the user once, then the
// service worker shows the inbox's bells while the app is closed.
import { call } from "frappe-ui";

const API = "periskope_connector.api.notifications.";

export const pushSupported = () =>
	"serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function keyBytes(base64) {
	const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4))
		.replace(/-/g, "+")
		.replace(/_/g, "/");
	return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function registration() {
	return (
		(await navigator.serviceWorker.getRegistration("/whatsapp/")) ||
		navigator.serviceWorker.register("/whatsapp/sw.js", { scope: "/whatsapp/" })
	);
}

// Whether this browser already gets the notifications.
export async function pushOn() {
	if (!pushSupported() || Notification.permission !== "granted") return false;
	const reg = await navigator.serviceWorker.getRegistration("/whatsapp/");
	return Boolean(reg && (await reg.pushManager.getSubscription()));
}

export async function turnPushOn(publicKey) {
	if ((await Notification.requestPermission()) !== "granted")
		throw new Error("Notifications are blocked for this site in the browser's settings.");
	const reg = await registration();
	await navigator.serviceWorker.ready;
	const sub =
		(await reg.pushManager.getSubscription()) ||
		(await reg.pushManager.subscribe({
			userVisibleOnly: true,
			applicationServerKey: keyBytes(publicKey),
		}));
	await call(API + "push_subscribe", {
		subscription: JSON.stringify(sub.toJSON()),
		user_agent: navigator.userAgent.slice(0, 140),
	});
}

export async function turnPushOff() {
	const reg = await navigator.serviceWorker.getRegistration("/whatsapp/");
	const sub = reg && (await reg.pushManager.getSubscription());
	if (!sub) return;
	await call(API + "push_unsubscribe", { endpoint: sub.endpoint });
	await sub.unsubscribe();
}
