<template>
	<div class="flex px-4 py-0.5" :class="outgoing ? 'justify-end' : 'justify-start'">
		<div
			class="max-w-[75%] rounded-lg px-3 py-1.5 shadow-sm"
			:class="
				outgoing ? 'bg-surface-green-1' : 'bg-surface-white border border-outline-gray-1'
			"
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
				{{ message.agent_reply === "Holding" ? "holding reply" : "reply" }}
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
						@click="open"
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
				<div
					v-if="message.body"
					class="whitespace-pre-wrap break-words text-base text-ink-gray-9"
				>
					{{ message.body }}
				</div>
				<div v-if="message.transcript" class="mt-1 text-sm italic text-ink-gray-6">
					“{{ message.transcript }}”
				</div>
				<div
					v-if="!message.body && !message.has_media && message.message_type !== 'text'"
					class="text-sm text-ink-gray-5"
				>
					[{{ message.message_type }}]
				</div>
			</template>

			<div class="mt-0.5 flex items-center justify-end gap-1 text-[11px] text-ink-gray-5">
				<span v-if="message.edited_on && !message.is_deleted">Edited</span>
				<span>{{ messageTime(message.message_timestamp) }}</span>
				<component
					:is="tick.icon"
					v-if="outgoing && tick"
					class="size-3.5"
					:class="tick.class"
					:title="tick.title"
				/>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";
import LucideBot from "~icons/lucide/bot";
import LucideBan from "~icons/lucide/ban";
import LucideFileText from "~icons/lucide/file-text";
import LucideCheck from "~icons/lucide/check";
import LucideCheckCheck from "~icons/lucide/check-check";
import LucideClock from "~icons/lucide/clock";
import { messageTime } from "@/utils/format";

const props = defineProps({
	message: { type: Object, required: true },
	isGroup: { type: Boolean, default: false },
	showSenderName: { type: Boolean, default: true },
});

const outgoing = computed(() => props.message.direction === "Outgoing");
const showSender = computed(() => props.isGroup && !outgoing.value && props.showSenderName);
const mediaUrl = computed(
	() =>
		`/api/method/periskope_connector.api.inbox.media?message=${encodeURIComponent(
			props.message.name
		)}`
);
const size = computed(() => {
	const b = props.message.media_size || 0;
	return b > 1048576
		? `${(b / 1048576).toFixed(1)} MB`
		: `${Math.max(1, Math.round(b / 1024))} KB`;
});

// Periskope's delivery state ("ack"): 0 pending, 1 sent, 2 delivered, 3 read, 4 played.
const tick = computed(() => {
	const ack = String(props.message.ack ?? "").toLowerCase();
	if (["3", "4", "read", "played"].includes(ack))
		return { icon: LucideCheckCheck, class: "text-ink-blue-3", title: "Read" };
	if (["2", "device", "delivered"].includes(ack))
		return { icon: LucideCheckCheck, class: "", title: "Delivered" };
	if (["1", "server", "sent"].includes(ack))
		return { icon: LucideCheck, class: "", title: "Sent" };
	if (["0", "pending"].includes(ack)) return { icon: LucideClock, class: "", title: "Waiting" };
	return { icon: LucideCheck, class: "", title: "Sent" };
});

function open() {
	window.open(mediaUrl.value, "_blank");
}
</script>
