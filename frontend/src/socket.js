import { io } from "socket.io-client";

// Frappe's socket.io: the server sends "periskope_inbox" with a chat name and number when a chat
// changes (inbox_realtime.py). Listeners reload what they show through the API.
const listeners = new Set();
// Frappe's own "notification" event: a new Notification Log for this user (the bell).
const bellListeners = new Set();

export function initSocket() {
	// Same scheme as the page (an http site has no https socket). On a port (bench start) the socket
	// server has its own port; behind nginx it is on the site's own address.
	const { protocol, hostname, port } = window.location;
	const socketPort = port ? `:${window.socketio_port || 9000}` : "";
	const url = `${protocol}//${hostname}${socketPort}/${window.site_name}`;
	const socket = io(url, { withCredentials: true, reconnectionDelayMax: 10000 });
	// Keep trying for good, and after a drop reload what is on screen: updates sent meanwhile were missed.
	let dropped = false;
	socket.on("disconnect", () => (dropped = true));
	socket.on("connect", () => {
		if (dropped) listeners.forEach((fn) => fn({ chat: null, reconnected: true }));
		dropped = false;
	});
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
