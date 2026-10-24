import { toast } from "frappe-ui";

// The message to show for a failed call: the server's own message when it sent one.
export function errorText(e, fallback = "That did not work. Please try again.") {
	return e?.messages?.[0] || e?.message || fallback;
}

// Tell the user a call failed (a toast), instead of failing silently.
export function showError(e, fallback) {
	toast.error(errorText(e, fallback));
}
