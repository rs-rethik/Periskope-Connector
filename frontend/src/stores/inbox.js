import { reactive, watch } from "vue";
import { call } from "frappe-ui";
import { onInboxUpdate } from "@/socket";

export const QUICK_FILTERS = [
	{ key: "all", label: "All open", filters: { status: "active" } },
	{ key: "mine", label: "My chats", filters: { status: "active", mine: 1 } },
	{ key: "unassigned", label: "Unassigned", filters: { status: "active", unassigned: 1 } },
	{ key: "unanswered", label: "Unanswered", filters: { status: "active", unanswered: 1 } },
	{ key: "unread", label: "Unread", filters: { unread: 1 } },
];

// The sidebar's choice (quick filter + number) is shared by the chat view and the list view, so
// switching between them keeps what the user was looking at.
export const inbox = reactive({
	quick: "all",
	number: "",
	counts: {},
	version: 0, // bumped on every live update; views watch it to reload
	changed: [], // the chats in the latest update
});

export function currentFilters(extra = {}) {
	const quick = QUICK_FILTERS.find((f) => f.key === inbox.quick) || QUICK_FILTERS[0];
	return { ...quick.filters, ...(inbox.number ? { number: inbox.number } : {}), ...extra };
}

export async function loadCounts() {
	inbox.counts = await call("periskope_connector.api.inbox.counts");
}

let timer;
let pending = new Set();
onInboxUpdate((data) => {
	// Several messages can land at once (a forwarded batch): reload once, for every chat that changed.
	pending.add(data.chat);
	clearTimeout(timer);
	timer = setTimeout(() => {
		inbox.changed = [...pending];
		pending = new Set();
		inbox.version++;
		loadCounts();
	}, 300);
});

watch(
	() => [inbox.quick, inbox.number],
	() => loadCounts()
);
