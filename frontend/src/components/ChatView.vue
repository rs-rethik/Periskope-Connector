<template>
	<div class="flex h-full min-w-0 flex-col bg-surface-gray-1">
		<div
			v-if="chat"
			class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-outline-gray-1 bg-surface-base px-4 py-2.5"
		>
			<Avatar :label="chat.chat_name || chat.contact_number || chat.title" size="lg" />
			<div class="min-w-[14rem] flex-1">
				<div class="flex items-center gap-2">
					<LucideUsers v-if="chat.is_group" class="size-4 shrink-0 text-ink-gray-5" />
					<span class="truncate text-base font-medium text-ink-gray-9">{{
						chat.title
					}}</span>
					<Badge :label="chat.status" variant="subtle" size="sm" />
				</div>
				<div class="truncate text-sm text-ink-gray-5">
					<span v-if="chat.contact_number">+{{ chat.contact_number }} · </span>
					on {{ chat.number_label }}
				</div>
			</div>
			<ChatActions ref="actions" :chat="chat" @changed="load({ keepPosition: true })" />
			<slot name="actions" />
		</div>

		<div ref="scroller" class="flex-1 overflow-y-auto py-3" @scroll="onScroll">
			<div v-if="hasMore" class="flex justify-center pb-2">
				<Button
					size="sm"
					variant="ghost"
					:loading="loadingOlder"
					label="Load earlier messages"
					@click="loadOlder"
				/>
			</div>
			<template v-for="(item, i) in timeline" :key="item.kind + item.name">
				<div
					v-if="i === 0 || !sameDay(timeline[i - 1].at, item.at)"
					class="my-2 flex justify-center"
				>
					<span
						class="rounded-full bg-surface-base px-3 py-0.5 text-xs text-ink-gray-6 shadow-sm"
					>
						{{ dayLabel(item.at) }}
					</span>
				</div>
				<div v-if="item.kind === 'note'" class="flex justify-center px-4 py-1">
					<div
						class="w-full max-w-[80%] rounded-lg border border-outline-amber-2 bg-surface-amber-1 px-3 py-1.5"
					>
						<div class="flex items-center gap-1 text-xs font-medium text-ink-gray-7">
							<LucideLock class="size-3" /> Private note · {{ item.note.owner_name }}
							<span class="ml-auto font-normal text-ink-gray-5">{{
								messageTime(item.at)
							}}</span>
						</div>
						<div class="whitespace-pre-wrap break-words text-sm text-ink-gray-8">
							{{ item.note.content }}
						</div>
					</div>
				</div>
				<MessageBubble
					v-else
					:message="item.message"
					:is-group="!!chat?.is_group"
					:show-sender-name="showName(i)"
					@reply="replyTo = $event"
					@updated="replace"
				/>
			</template>
			<div v-if="loading" class="flex justify-center p-6">
				<LoadingIndicator class="size-5" />
			</div>
			<div v-else-if="error" class="p-6 text-center text-sm text-ink-gray-6">
				{{ error }}
			</div>
			<div v-else-if="!timeline.length" class="p-6 text-center text-sm text-ink-gray-5">
				No messages yet.
			</div>
		</div>

		<Composer
			v-if="chat && !error"
			ref="composer"
			:chat="chat"
			:reply-to="replyTo"
			@sent="addMessage"
			@noted="addNote"
			@clear-reply="replyTo = null"
		/>
	</div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Avatar, Badge, Button, LoadingIndicator, call } from "frappe-ui";
import LucideUsers from "~icons/lucide/users";
import LucideLock from "~icons/lucide/lock";
import MessageBubble from "@/components/MessageBubble.vue";
import Composer from "@/components/Composer.vue";
import ChatActions from "@/components/ChatActions.vue";
import { inbox } from "@/stores/inbox";
import { dayLabel, messageTime, sameDay } from "@/utils/format";

const props = defineProps({ name: { type: String, required: true } });
const emit = defineEmits(["loaded"]);

const chat = ref(null);
const messages = ref([]);
const notes = ref([]);
const hasMore = ref(false);
const loading = ref(false);
const loadingOlder = ref(false);
const error = ref("");
const scroller = ref(null);
const composer = ref(null);
const replyTo = ref(null);
const actions = ref(null);

// Messages and the team's private notes, in time order.
const timeline = computed(() =>
	[
		...messages.value.map((m) => ({
			kind: "message",
			name: m.name,
			at: m.message_timestamp,
			message: m,
		})),
		...notes.value.map((n) => ({ kind: "note", name: n.name, at: n.creation, note: n })),
	].sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0))
);

function showName(i) {
	const prev = timeline.value[i - 1];
	const m = timeline.value[i].message;
	return (
		!prev ||
		prev.kind !== "message" ||
		prev.message.sender_phone !== m.sender_phone ||
		prev.message.direction !== m.direction
	);
}

function nearBottom() {
	const el = scroller.value;
	return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120;
}

function scrollToBottom() {
	nextTick(() => {
		if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
	});
}

function mergeNotes(list) {
	const byName = new Map(notes.value.map((n) => [n.name, n]));
	for (const n of list) byName.set(n.name, n);
	notes.value = [...byName.values()];
}

async function load({ keepPosition = false } = {}) {
	const stick = !keepPosition || nearBottom();
	loading.value = !messages.value.length;
	try {
		const [c, r] = await Promise.all([
			call("periskope_connector.api.inbox.get_chat", { chat: props.name }),
			call("periskope_connector.api.inbox.get_messages", { chat: props.name }),
		]);
		chat.value = c;
		emit("loaded", c);
		// Keep the older pages already loaded; the newest page replaces the tail.
		const first = r.messages[0]?.message_timestamp;
		const older =
			keepPosition && first ? messages.value.filter((m) => m.message_timestamp < first) : [];
		messages.value = [...older, ...r.messages];
		mergeNotes(r.notes || []);
		if (!older.length) hasMore.value = r.has_more;
		if (stick) scrollToBottom();
		error.value = "";
		call("periskope_connector.api.inbox.mark_seen", { chat: props.name });
		if (!keepPosition && hasPendingTicks()) refreshTicks();
	} catch (e) {
		error.value =
			e?.exc_type === "PermissionError" ||
			/PermissionError|403/.test(String(e?.message || e))
				? "You cannot open this chat. It may be on a number or for a customer that is not yours."
				: "This chat could not be loaded.";
	} finally {
		loading.value = false;
	}
}

async function loadOlder() {
	if (!messages.value.length) return;
	loadingOlder.value = true;
	const el = scroller.value;
	const before = el.scrollHeight;
	try {
		const r = await call("periskope_connector.api.inbox.get_messages", {
			chat: props.name,
			before: messages.value[0].message_timestamp,
		});
		messages.value = [...r.messages, ...messages.value];
		mergeNotes(r.notes || []);
		hasMore.value = r.has_more;
		await nextTick();
		el.scrollTop = el.scrollHeight - before;
	} finally {
		loadingOlder.value = false;
	}
}

function onScroll() {
	if (scroller.value?.scrollTop < 60 && hasMore.value && !loadingOlder.value) loadOlder();
}

function addMessage(m) {
	if (!messages.value.some((x) => x.name === m.name)) messages.value = [...messages.value, m];
	scrollToBottom();
	// Where no webhook arrives, ask Periskope for the ticks shortly after the send.
	for (const seconds of [5, 20, 60]) timers.push(setTimeout(refreshTicks, seconds * 1000));
}

const timers = [];

// Replies of ours that Periskope has taken but are not read yet.
function hasPendingTicks() {
	return messages.value.some(
		(m) => m.send_status === "Queued" && !["4", "5"].includes(String(m.ack ?? ""))
	);
}

async function refreshTicks() {
	if (!hasPendingTicks()) return;
	try {
		const updated = await call("periskope_connector.api.compose.refresh_chat_ticks", {
			chat: props.name,
		});
		if (updated) load({ keepPosition: true });
	} catch (e) {
		// the ticks stay as they are; the webhook or the next try updates them
	}
}

function addNote(n) {
	mergeNotes([n]);
	scrollToBottom();
}

function replace(m) {
	messages.value = messages.value.map((x) => (x.name === m.name ? { ...x, ...m } : x));
}

// Keyboard: r reply, a assign to me, e resolve.
function onKey(e) {
	if (e.metaKey || e.ctrlKey || e.altKey) return;
	if (e.target.closest("input, textarea, [contenteditable]")) return;
	const keys = {
		r: () => composer.value?.focus(),
		a: () => actions.value?.assignToMe(),
		e: () => actions.value?.resolve(),
	};
	if (!keys[e.key]) return;
	e.preventDefault();
	keys[e.key]();
}

watch(
	() => props.name,
	() => {
		messages.value = [];
		notes.value = [];
		chat.value = null;
		replyTo.value = null;
		load();
	}
);
watch(
	() => inbox.version,
	() => {
		if (inbox.changed.includes(props.name)) load({ keepPosition: true });
	}
);
onMounted(() => {
	load();
	window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => {
	window.removeEventListener("keydown", onKey);
	timers.forEach(clearTimeout);
});

defineExpose({ reload: () => load({ keepPosition: true }), chat });
</script>
