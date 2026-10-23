import { io } from "socket.io-client";

// Frappe's socket.io: the server sends "periskope_inbox" with a chat name and number when a chat
// changes (inbox_realtime.py). Listeners reload what they show through the API.
const listeners = new Set();
// Frappe's own "notification" event: a new Notification Log for this user (the bell).
const bellListeners = new Set();

export function initSocket() {
	const port = window.location.port ? `:${window.socketio_port || 9000}` : "";
	const protocol = port ? "http" : "https";
	const url = `${protocol}://${window.location.hostname}${port}/${window.site_name}`;
	const socket = io(url, { withCredentials: true, reconnectionAttempts: 5 });
	socket.on("periskope_inbox", (data) => listeners.forEach((fn) => fn(data)));
	socket.on("notification", () => bellListeners.forEach((fn) => fn()));
	return socket;
}

export function onInboxUpdate(fn) {
	listeners.add(fn);
	return () => listeners.delete(fn);
}

export function onNotification(fn) {
	bellListeners.add(fn);
	return () => bellListeners.delete(fn);
}
