import { reactive, watch } from "vue";
import { call } from "frappe-ui";
import { onInboxUpdate, onNotification } from "@/socket";
import { loadFailed } from "@/utils/load";

export const QUICK_FILTERS = [
	{ key: "all", label: "All open", filters: { status: "active" } },
	{ key: "mine", label: "My chats", filters: { status: "active", mine: 1 } },
	{ key: "unassigned", label: "Unassigned", filters: { status: "active", unassigned: 1 } },
	{ key: "unanswered", label: "Unanswered", filters: { status: "active", unanswered: 1 } },
	{ key: "unread", label: "Unread", filters: { unread: 1 } },
	{ key: "breached", label: "SLA breached", filters: { sla: "breached" } },
	{ key: "my_team", label: "My team", filters: { status: "active", my_team: 1 } },
];

// The sidebar's choice (quick filter + number) is shared by the chat view and the list view, so
// switching between them keeps what the user was looking at.
export const inbox = reactive({
	quick: "all",
	number: "",
	counts: {},
	version: 0, // bumped on every live update; views watch it to reload
	changed: [], // the chats in the latest update
	views: [], // saved list views (own and shared)
	bell: { notifications: [], unread: 0 },
	teams: [],
});

// Chats opened recently (ChatView), kept for the session so reopening one is instant.
export const chatCache = new Map();

// Unsent text and files per chat (Composer), so a draft stays with its own chat.
export const drafts = new Map();

export async function loadViews() {
	try {
		inbox.views = await call("periskope_connector.api.views.list_views");
	} catch (e) {
		loadFailed(e, "Could not load the saved views.");
	}
}

export async function loadBell() {
	try {
		inbox.bell = await call("periskope_connector.api.notifications.get_notifications");
	} catch (e) {
		loadFailed(e, "Could not load the notifications.");
	}
}

export async function loadTeams() {
	try {
		inbox.teams = await call("periskope_connector.api.chat_actions.teams");
	} catch (e) {
		loadFailed(e, "Could not load the teams.");
	}
}

onNotification(() => loadBell());

export function currentFilters(extra = {}) {
	const quick = QUICK_FILTERS.find((f) => f.key === inbox.quick) || QUICK_FILTERS[0];
	return { ...quick.filters, ...(inbox.number ? { number: inbox.number } : {}), ...extra };
}

export async function loadCounts() {
	try {
		inbox.counts = await call("periskope_connector.api.inbox.counts");
	} catch (e) {
		loadFailed(e, "Could not load the counts.");
	}
}

let timer;
let pending = new Set();
// Whether a live update touched this chat; after a reconnect every chat counts as changed.
export function changed(chat) {
	return inbox.changed.includes(chat) || inbox.changed.includes("*");
}

onInboxUpdate((data) => {
	// Several messages can land at once (a forwarded batch): reload once, for every chat that changed.
	pending.add(data.reconnected ? "*" : data.chat);
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
