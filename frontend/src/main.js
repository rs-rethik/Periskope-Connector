import "./index.css";
import "./theme.css";

import { createApp } from "vue";
import { FrappeUI, setConfig, frappeRequest } from "frappe-ui";
import router from "./router";
import App from "./App.vue";
import { initSocket } from "./socket";

setConfig("resourceFetcher", frappeRequest);

const app = createApp(App);
app.use(FrappeUI, { socketio: false }); // socket.js opens the one socket, on the site's port
app.use(router);

// Installable app: the service worker (only in a build; the dev server has no /whatsapp/sw.js).
if ("serviceWorker" in navigator && !import.meta.env.DEV) {
	window.addEventListener("load", () => {
		navigator.serviceWorker
			.register("/whatsapp/sw.js", { scope: "/whatsapp/" })
			.catch(() => {});
	});
}

// The phone's status bar follows the app's light or dark theme.
const themeColor = document.querySelector('meta[name="theme-color"]');
function paintStatusBar() {
	const dark = document.documentElement.getAttribute("data-theme") === "dark";
	themeColor?.setAttribute("content", dark ? "#0f1817" : "#eef3f0");
}
new MutationObserver(paintStatusBar).observe(document.documentElement, {
	attributes: true,
	attributeFilter: ["data-theme"],
});

function start() {
	initSocket();
	app.mount("#app");
}

if (import.meta.env.DEV) {
	// `yarn dev`: fetch the boot data the built page would carry.
	frappeRequest({
		url: "/api/method/periskope_connector.www.whatsapp.get_context_for_dev",
	}).then((values) => {
		for (const key in values) window[key] = values[key];
		start();
	});
} else {
	start();
}
