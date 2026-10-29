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
