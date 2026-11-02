import { toast } from "frappe-ui";
import { errorText } from "@/utils/errors";

// What a failed background load does (lists, counts, the bell, settings): when the session has
// expired the user goes to the login page and comes back here after; any other failure shows one
// toast per kind of load (not one every live update), instead of a silently empty screen.
const lastToast = {};
let checking = null;

async function sessionGone() {
	checking ||= fetch("/api/method/frappe.auth.get_logged_user", { credentials: "same-origin" })
		.then(
			async (r) =>
				r.status === 401 || r.status === 403 || (await r.json()).message === "Guest"
		)
		.catch(() => false)
		.finally(() => setTimeout(() => (checking = null), 5000));
	return checking;
}

export async function loadFailed(e, what = "Could not load this. Check your connection.") {
	if (await sessionGone()) {
		const here = window.location.pathname + window.location.search;
		window.location.href = `/login?redirect-to=${encodeURIComponent(here)}`;
		return;
	}
	const now = Date.now();
	if (now - (lastToast[what] || 0) < 30000) return;
	lastToast[what] = now;
	toast.error(errorText(e, what));
}
