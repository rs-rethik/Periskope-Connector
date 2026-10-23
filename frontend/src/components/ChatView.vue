<template>
	<div class="flex h-full min-w-0 flex-col bg-surface-gray-1">
		<div
			v-if="chat"
			class="flex items-center gap-3 border-b border-outline-gray-1 bg-surface-white px-4 py-2.5"
		>
			<Avatar :label="chat.chat_name || chat.contact_number || chat.title" size="lg" />
			<div class="min-w-0 flex-1">
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
			<template v-for="(m, i) in messages" :key="m.name">
				<div
					v-if="
						i === 0 || !sameDay(messages[i - 1].message_timestamp, m.message_timestamp)
					"
					class="my-2 flex justify-center"
				>
					<span
						class="rounded-full bg-surface-white px-3 py-0.5 text-xs text-ink-gray-6 shadow-sm"
					>
						{{ dayLabel(m.message_timestamp) }}
					</span>
				</div>
				<MessageBubble
					:message="m"
					:is-group="!!chat?.is_group"
					:show-sender-name="
						i === 0 ||
						messages[i - 1].sender_phone !== m.sender_phone ||
						messages[i - 1].direction !== m.direction
					"
				/>
			</template>
			<div v-if="loading" class="flex justify-center p-6">
				<LoadingIndicator class="size-5" />
			</div>
			<div v-else-if="error" class="p-6 text-center text-sm text-ink-gray-6">
				{{ error }}
			</div>
			<div v-else-if="!messages.length" class="p-6 text-center text-sm text-ink-gray-5">
				No messages yet.
			</div>
		</div>

		<div
			class="border-t border-outline-gray-1 bg-surface-white px-4 py-3 text-sm text-ink-gray-5"
		>
			<slot name="composer"
				>Replying from here comes in the next step. Messages update live.</slot
			>
		</div>
	</div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from "vue";
import { Avatar, Badge, Button, LoadingIndicator, call } from "frappe-ui";
import LucideUsers from "~icons/lucide/users";
import MessageBubble from "@/components/MessageBubble.vue";
import { inbox } from "@/stores/inbox";
import { dayLabel, sameDay } from "@/utils/format";

const props = defineProps({ name: { type: String, required: true } });
const emit = defineEmits(["loaded"]);

const chat = ref(null);
const messages = ref([]);
const hasMore = ref(false);
const loading = ref(false);
const loadingOlder = ref(false);
const error = ref("");
const scroller = ref(null);

function nearBottom() {
	const el = scroller.value;
	return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120;
}

function scrollToBottom() {
	nextTick(() => {
		if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
	});
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
		if (!older.length) hasMore.value = r.has_more;
		if (stick) scrollToBottom();
		error.value = "";
		call("periskope_connector.api.inbox.mark_seen", { chat: props.name });
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

watch(
	() => props.name,
	() => {
		messages.value = [];
		chat.value = null;
		load();
	}
);
watch(
	() => inbox.version,
	() => {
		if (inbox.changed.includes(props.name)) load({ keepPosition: true });
	}
);
onMounted(() => load());

defineExpose({ reload: () => load({ keepPosition: true }), chat });
</script>
