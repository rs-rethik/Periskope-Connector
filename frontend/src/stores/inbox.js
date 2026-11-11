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
	{ key: "review", label: "Check CRM link", filters: { status: "active", review: 1 } },
];

// The sidebar's choice (quick filter + number) is shared by the chat view and the list view, so
// switching between them keeps what the user was looking at.
export const inbox = reactive({
	quick: "all",
	number: "",
	counts: {},
	version: 0, // bumped on every live update; views watch it to reload
	changed: [], // the chats in the latest update
	viewing: "", // the chat open in ChatView
	tickVersion: 0, // bumped when delivery ticks change; only the open chat reloads
	ticked: [], // the chats whose ticks changed
	views: [], // saved list views (own and shared)
	bell: { notifications: [], unread: 0 },
	teams: [],
});

// Chats opened recently (ChatView), kept for the session so reopening one is instant.
export const chatCache = new Map();

// Unsent text and files per chat (Composer), so a draft stays with its own chat. Also kept in the
// tab's session storage: a phone that drops the installed app from memory gives the drafts back.
const DRAFTS_KEY = "periskope-drafts";
function storedDrafts() {
	try {
		return new Map(Object.entries(JSON.parse(sessionStorage.getItem(DRAFTS_KEY) || "{}")));
	} catch {
		return new Map(); // no session storage, or not ours: start without drafts
	}
}
export const drafts = storedDrafts();

// Keep (or, with nothing, forget) a chat's draft.
export function saveDraft(chat, draft = null) {
	if (draft) drafts.set(chat, draft);
	else drafts.delete(chat);
	try {
		sessionStorage.setItem(DRAFTS_KEY, JSON.stringify(Object.fromEntries(drafts)));
	} catch {
		// no session storage: the drafts last while the page is open
	}
}

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

// The update is about the open chat only, in a tab that is looked at: ChatView reloads it, marks it
// read and loads the counts after that, so they are not loaded here as well.
export function viewedAlone() {
	return !document.hidden && inbox.changed.length === 1 && inbox.changed[0] === inbox.viewing;
}

let tickTimer;
let ticked = new Set();
onInboxUpdate((data) => {
	if (data.kind === "ack" && !data.reconnected) {
		// delivery ticks change only the open conversation: no list, counter or panel reload
		ticked.add(data.chat);
		clearTimeout(tickTimer);
		tickTimer = setTimeout(() => {
			inbox.ticked = [...ticked];
			ticked = new Set();
			inbox.tickVersion++;
		}, 300);
		return;
	}
	// Several messages can land at once (a forwarded batch): reload once, for every chat that changed.
	pending.add(data.reconnected ? "*" : data.chat);
	clearTimeout(timer);
	timer = setTimeout(() => {
		inbox.changed = [...pending];
		pending = new Set();
		inbox.version++;
		if (!viewedAlone()) loadCounts();
	}, 300);
});

watch(
	() => [inbox.quick, inbox.number],
	() => loadCounts()
);
