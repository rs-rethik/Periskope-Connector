import { io } from "socket.io-client";

// Frappe's socket.io: the server sends "periskope_inbox" with a chat name and number when a chat
// changes (inbox_realtime.py). Listeners reload what they show through the API.
const listeners = new Set();

export function initSocket() {
	const port = window.location.port ? `:${window.socketio_port || 9000}` : "";
	const protocol = port ? "http" : "https";
	const url = `${protocol}://${window.location.hostname}${port}/${window.site_name}`;
	const socket = io(url, { withCredentials: true, reconnectionAttempts: 5 });
	socket.on("periskope_inbox", (data) => listeners.forEach((fn) => fn(data)));
	return socket;
}

export function onInboxUpdate(fn) {
	listeners.add(fn);
	return () => listeners.delete(fn);
}
