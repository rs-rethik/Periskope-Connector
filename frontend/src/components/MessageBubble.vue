<template>
	<div
		class="group flex items-center gap-1 px-4 py-0.5"
		:class="outgoing ? 'justify-end' : 'justify-start'"
	>
		<Tooltip v-if="outgoing && canReply" text="Reply to this message"
			><button
				class="invisible rounded p-1 text-ink-gray-5 hover:bg-surface-gray-2 group-hover:visible"
				aria-label="Reply to this message"
				@click="$emit('reply', message)"
			>
				<LucideReply class="size-4" /></button
		></Tooltip>
		<div
			class="relative max-w-[75%] rounded-2xl px-3.5 py-2 shadow-[0_1px_2px_rgb(0_0_0/0.06),0_6px_16px_-10px_rgb(0_0_0/0.18)]"
			:class="[
				outgoing ? 'bg-surface-green-2' : 'bg-surface-base',
				unsent ? 'ring-1 ring-outline-red-2' : '',
				hasReactions ? 'mb-3' : '',
			]"
		>
			<div v-if="showSender" class="mb-0.5 text-xs font-medium text-ink-blue-6">
				{{
					message.sender_label ||
					(message.sender_phone ? "+" + message.sender_phone : "")
				}}
			</div>
			<div
				v-else-if="message.agent_reply"
				class="mb-0.5 flex items-center gap-1 text-xs font-medium text-ink-violet-6"
			>
				<LucideBot class="size-3" /> AI
				{{
					{ Holding: "holding reply", Welcome: "welcome" }[message.agent_reply] ||
					"reply"
				}}
			</div>
			<div
				v-else-if="outgoing && message.sender_label"
				class="mb-0.5 text-xs font-medium text-ink-gray-6"
			>
				{{ message.sender_label }}
			</div>

			<div
				v-if="message.quoted"
				class="mb-1 rounded border-l-4 border-outline-gray-3 bg-surface-gray-1 px-2 py-1 text-xs text-ink-gray-6"
			>
				<div class="font-medium">
					{{ message.quoted.direction === "Outgoing" ? "You" : "Customer" }}
				</div>
				<div class="line-clamp-2">
					{{ message.quoted.body || "[" + message.quoted.message_type + "]" }}
				</div>
			</div>

			<div
				v-if="message.is_deleted"
				class="flex items-center gap-1 text-sm italic text-ink-gray-5"
			>
				<LucideBan class="size-3.5" /> This message was deleted
			</div>
			<template v-else>
				<template v-if="message.has_media">
					<img
						v-if="message.message_type === 'image'"
						:src="mediaUrl"
						class="mb-1 max-h-72 cursor-zoom-in rounded"
						loading="lazy"
						alt=""
						@click="$emit('view', message)"
					/>
					<audio
						v-else-if="message.message_type === 'audio'"
						:src="mediaUrl"
						controls
						preload="none"
						class="mb-1 w-64"
					/>
					<video
						v-else-if="message.message_type === 'video'"
						:src="mediaUrl"
						controls
						preload="none"
						class="mb-1 max-h-72 rounded"
					/>
					<a
						v-else
						:href="mediaUrl"
						target="_blank"
						class="mb-1 flex items-center gap-2 rounded bg-surface-gray-1 px-2 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
					>
						<LucideFileText class="size-4 shrink-0" />
						<span class="truncate">{{ message.media_filename || "Document" }}</span>
						<span v-if="message.media_size" class="shrink-0 text-xs text-ink-gray-5">{{
							size
						}}</span>
					</a>
				</template>
				<a
					v-else-if="message.message_type === 'location'"
					:href="mapsUrl"
					target="_blank"
					rel="noopener"
					class="mb-1 flex w-64 items-start gap-2 rounded bg-surface-gray-1 px-2 py-2 hover:bg-surface-gray-2"
					:class="mapsUrl ? '' : 'pointer-events-none'"
				>
					<LucideMapPin class="mt-0.5 size-5 shrink-0 text-ink-red-6" />
					<span class="min-w-0 text-sm">
						<span class="block font-medium text-ink-gray-9">{{
							shared.name || "Location"
						}}</span>
						<span v-if="shared.address" class="block text-ink-gray-6">{{
							shared.address
						}}</span>
						<span v-if="mapsUrl" class="block text-ink-blue-6">Open in Maps</span>
					</span>
				</a>
				<div
					v-else-if="message.message_type === 'contact'"
					class="mb-1 flex w-64 flex-col gap-1.5"
				>
					<div
						v-for="(c, i) in shared.contacts || [{ name: message.body }]"
						:key="i"
						class="flex items-center gap-2 rounded bg-surface-gray-1 px-2 py-2"
					>
						<Avatar :label="c.name || '?'" size="md" />
						<div class="min-w-0 text-sm">
							<div class="truncate font-medium text-ink-gray-9">
								{{ c.name || "Contact" }}
							</div>
							<a
								v-for="p in c.phones || []"
								:key="p.number"
								:href="'tel:' + p.number.replace(/[^\d+]/g, '')"
								class="block text-ink-gray-6 hover:text-ink-gray-8"
								>{{ p.number }}</a
							>
						</div>
					</div>
				</div>
				<div v-else-if="message.message_type === 'poll'" class="mb-1 w-64 text-sm">
					<div class="mb-1 flex items-center gap-1 font-medium text-ink-gray-9">
						<LucideChartBar class="size-4 shrink-0" />
						{{ shared.question || message.body || "Poll" }}
					</div>
					<div
						v-for="o in shared.options || []"
						:key="o"
						class="mt-1 flex items-center justify-between gap-2 rounded bg-surface-gray-1 px-2 py-1"
					>
						<span class="min-w-0 break-words">{{ o }}</span>
						<span v-if="shared.votes" class="shrink-0 text-xs text-ink-gray-5">{{
							shared.votes[o] || 0
						}}</span>
					</div>
				</div>
				<div
					v-if="message.body && !(message.message_type in SHARED_KINDS)"
					class="whitespace-pre-wrap break-words text-base text-ink-gray-9"
				>
					{{ message.body }}
				</div>
				<div v-if="message.transcript" class="mt-1 text-sm italic text-ink-gray-6">
					“{{ message.transcript }}”
				</div>
				<div
					v-if="
						!message.has_media &&
						!(message.message_type in SHARED_KINDS) &&
						!['text', 'other'].includes(message.message_type) &&
						!message.body
					"
					class="text-sm text-ink-gray-5"
				>
					[{{ message.message_type }}]
				</div>
				<div
					v-else-if="message.message_type === 'other' && !message.body"
					class="text-sm italic text-ink-gray-5"
				>
					This kind of message can't be shown here. Open it in WhatsApp.
				</div>
			</template>

			<div class="mt-0.5 flex items-center justify-end gap-1 text-[11px] text-ink-gray-5">
				<span v-if="message.edited_on && !message.is_deleted">Edited</span>
				<span>{{ messageTime(message.message_timestamp) }}</span>
				<Tooltip v-if="outgoing && tick" :text="tick.title"
					><component :is="tick.icon" class="size-3.5" :class="tick.class"
				/></Tooltip>
			</div>
			<div v-if="unsent" class="mt-1 flex items-center gap-2 text-xs text-ink-red-8">
				<LucideCircleAlert class="size-3.5 shrink-0" />
				<span class="min-w-0 flex-1">
					{{ message.send_status === "Not Sent" ? "Not sent" : "Failed" }}:
					{{ message.send_error || "Periskope did not take it." }}
				</span>
				<Button
					v-if="message.send_status === 'Failed'"
					size="sm"
					variant="subtle"
					label="Retry"
					:loading="retrying"
					@click="retry"
				/>
			</div>
			<div v-if="message.deals?.length" class="mt-1.5 flex flex-wrap gap-1">
				<Tooltip
					v-for="d in message.deals"
					:key="d"
					text="A deal was created from this message"
					><a
						:href="`/crm/deals/${d}`"
						target="_blank"
						class="inline-flex items-center gap-1 rounded-full bg-surface-violet-1 px-2 py-0.5 text-xs font-medium text-ink-violet-7 hover:bg-surface-violet-2"
						aria-label="A deal was created from this message"
					>
						<LucideHandshake class="size-3" />{{ d }}
					</a></Tooltip
				>
			</div>
			<Tooltip v-if="hasReactions" :text="reactionTitle"
				><div
					class="absolute -bottom-3 flex gap-0.5 rounded-full border border-outline-gray-1 bg-surface-base px-1.5 py-0.5 text-sm shadow-sm"
					:class="outgoing ? 'right-2' : 'left-2'"
				>
					<span v-for="(n, e) in reactionCounts" :key="e"
						>{{ e
						}}<span v-if="n > 1" class="text-xs text-ink-gray-5">{{ n }}</span></span
					>
				</div></Tooltip
			>
		</div>
		<Tooltip v-if="!outgoing && canReply" text="Reply to this message"
			><button
				class="invisible rounded p-1 text-ink-gray-5 hover:bg-surface-gray-2 group-hover:visible"
				aria-label="Reply to this message"
				@click="$emit('reply', message)"
			>
				<LucideReply class="size-4" /></button
		></Tooltip>
		<Tooltip v-if="!outgoing && canCreateDeal" text="Create a deal from the customer's message"
			><button
				class="invisible rounded p-1 text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-violet-7 group-hover:visible"
				aria-label="Create a deal from the customer's message"
				@click="$emit('create-deal', message)"
			>
				<LucideHandshake class="size-4" /></button
		></Tooltip>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import { Avatar, Button, Tooltip, call } from "frappe-ui";
import LucideBot from "~icons/lucide/bot";
import LucideHandshake from "~icons/lucide/handshake";
import LucideBan from "~icons/lucide/ban";
import LucideFileText from "~icons/lucide/file-text";
import LucideMapPin from "~icons/lucide/map-pin";
import LucideChartBar from "~icons/lucide/chart-bar";
import LucideCheck from "~icons/lucide/check";
import LucideCheckCheck from "~icons/lucide/check-check";
import LucideClock from "~icons/lucide/clock";
import LucideReply from "~icons/lucide/reply";
import LucideCircleAlert from "~icons/lucide/circle-alert";
import { messageTime } from "@/utils/format";
import { showError } from "@/utils/errors";
import { mediaUrl as messageMediaUrl } from "@/utils/media";

const props = defineProps({
	message: { type: Object, required: true },
	isGroup: { type: Boolean, default: false },
	showSenderName: { type: Boolean, default: true },
	canCreateDeal: { type: Boolean, default: false },
});
const emit = defineEmits(["reply", "updated", "view", "create-deal"]);

const outgoing = computed(() => props.message.direction === "Outgoing");
const showSender = computed(() => props.isGroup && !outgoing.value && props.showSenderName);
const mediaUrl = computed(() => messageMediaUrl(props.message));

// A shared location, contact card or poll: its parts come from the server as `shared`. Their body is
// the card's own text (or a map picture), so it is not shown again below.
const SHARED_KINDS = { location: 1, contact: 1, poll: 1 };
const shared = computed(() => props.message.shared || {});
const mapsUrl = computed(() => {
	const s = shared.value;
	if (s.latitude != null && s.longitude != null)
		return `https://www.google.com/maps/search/?api=1&query=${s.latitude},${s.longitude}`;
	if (s.url) return s.url;
	const q = [s.name, s.address].filter(Boolean).join(", ");
	return q ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}` : "";
});
const size = computed(() => {
	const b = props.message.media_size || 0;
	return b > 1048576
		? `${(b / 1048576).toFixed(1)} MB`
		: `${Math.max(1, Math.round(b / 1024))} KB`;
});

const unsent = computed(() => ["Failed", "Not Sent"].includes(props.message.send_status));
// A reply quotes a message WhatsApp knows: not one still being sent or that never went.
const canReply = computed(
	() =>
		!props.message.is_deleted &&
		!(props.message.message_id || "").startsWith("local_") &&
		!unsent.value
);

// Periskope's delivery state ("ack"): -1 failed, 0 waiting, 1-2 sent, 3 delivered, 4-5 read.
const tick = computed(() => {
	if (props.message.send_status === "Sending")
		return { icon: LucideClock, class: "", title: "Sending" };
	if (unsent.value) return null;
	const ack = String(props.message.ack ?? "").toLowerCase();
	if (["4", "5", "read", "played"].includes(ack))
		return { icon: LucideCheckCheck, class: "text-ink-blue-6", title: "Read" };
	if (["3", "delivered"].includes(ack))
		return { icon: LucideCheckCheck, class: "", title: "Delivered" };
	if (["0", "pending"].includes(ack))
		return { icon: LucideClock, class: "", title: "Waiting to send" };
	return { icon: LucideCheck, class: "", title: "Sent" };
});

const reactions = computed(() => props.message.reactions || {});
const hasReactions = computed(() => Object.keys(reactions.value).length > 0);
const reactionCounts = computed(() => {
	const out = {};
	for (const e of Object.values(reactions.value)) out[e] = (out[e] || 0) + 1;
	return out;
});
const reactionTitle = computed(() =>
	Object.entries(reactions.value)
		.map(([who, e]) => `${e} +${who}`)
		.join(", ")
);

const retrying = ref(false);
async function retry() {
	if (retrying.value) return;
	retrying.value = true;
	try {
		const m = await call("periskope_connector.api.compose.retry", {
			message: props.message.name,
		});
		emit("updated", m);
		if (["Failed", "Not Sent"].includes(m.send_status))
			showError(null, m.send_error || "Still not sent.");
	} catch (e) {
		showError(e, "The message could not be sent again.");
	} finally {
		retrying.value = false;
	}
}
</script>
