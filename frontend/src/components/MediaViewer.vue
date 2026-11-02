<template>
	<Teleport to="body">
		<div
			v-if="current"
			class="fixed inset-0 z-50 flex flex-col bg-black text-white"
			role="dialog"
			aria-modal="true"
			@click.self="close"
		>
			<div class="flex items-center gap-3 px-4 py-3">
				<div class="min-w-0 flex-1">
					<div class="truncate text-base font-medium">{{ sender }}</div>
					<div class="text-sm text-white/60">
						{{ dayLabel(current.message_timestamp) }} at
						{{ messageTime(current.message_timestamp) }}
					</div>
				</div>
				<Tooltip text="Download"
					><a
						:href="url(current)"
						:download="current.media_filename || current.name"
						class="rounded-full p-2 hover:bg-white/10"
						aria-label="Download"
					>
						<LucideDownload class="size-5" /> </a
				></Tooltip>
				<Tooltip text="Close (Esc)"
					><button
						class="rounded-full p-2 hover:bg-white/10"
						aria-label="Close (Esc)"
						@click="close"
					>
						<LucideX class="size-5" /></button
				></Tooltip>
			</div>

			<div
				class="relative flex min-h-0 flex-1 items-center justify-center px-14"
				@click.self="close"
			>
				<Tooltip v-if="index > 0" text="Previous (←)"
					><button
						class="absolute left-3 rounded-full bg-white/10 p-2 hover:bg-white/20"
						aria-label="Previous (←)"
						@click="index--"
					>
						<LucideChevronLeft class="size-6" /></button
				></Tooltip>
				<img
					:key="current.name"
					:src="url(current)"
					class="max-h-full max-w-full select-none object-contain"
					alt=""
				/>
				<Tooltip v-if="index < images.length - 1" text="Next (→)"
					><button
						class="absolute right-3 rounded-full bg-white/10 p-2 hover:bg-white/20"
						aria-label="Next (→)"
						@click="index++"
					>
						<LucideChevronRight class="size-6" /></button
				></Tooltip>
			</div>

			<div
				v-if="current.body"
				class="mx-auto max-w-2xl whitespace-pre-wrap break-words px-4 pt-3 text-center text-base"
			>
				{{ current.body }}
			</div>

			<div v-if="images.length > 1" class="flex justify-center gap-2 overflow-x-auto p-3">
				<button
					v-for="(m, i) in images"
					:key="m.name"
					class="size-14 shrink-0 overflow-hidden rounded border-2"
					:class="
						i === index
							? 'border-white'
							: 'border-transparent opacity-60 hover:opacity-100'
					"
					@click="index = i"
				>
					<img :src="url(m)" class="size-full object-cover" loading="lazy" alt="" />
				</button>
			</div>
		</div>
	</Teleport>
</template>

<script setup>
import { Tooltip } from "frappe-ui";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import LucideX from "~icons/lucide/x";
import LucideDownload from "~icons/lucide/download";
import LucideChevronLeft from "~icons/lucide/chevron-left";
import LucideChevronRight from "~icons/lucide/chevron-right";
import { dayLabel, messageTime } from "@/utils/format";
import { mediaUrl as url } from "@/utils/media";

// Opens on the image message named by v-model; the arrows step through the chat's other images.
const props = defineProps({
	images: { type: Array, required: true },
	chatTitle: { type: String, default: "" },
});
const open = defineModel({ type: String, default: null });

const index = ref(0);
const current = computed(() => (open.value ? props.images[index.value] : null));
const sender = computed(() => {
	const m = current.value;
	if (m.direction === "Outgoing") return m.sender_label || "You";
	return m.sender_label || (m.sender_phone ? "+" + m.sender_phone : props.chatTitle);
});

watch(open, (name) => {
	if (!name) return;
	index.value = Math.max(
		0,
		props.images.findIndex((m) => m.name === name)
	);
});

function close() {
	open.value = null;
}

function onKey(e) {
	if (!open.value) return;
	if (e.key === "Escape") close();
	else if (e.key === "ArrowLeft" && index.value > 0) index.value--;
	else if (e.key === "ArrowRight" && index.value < props.images.length - 1) index.value++;
	else return;
	// Handled here, so the chat's own Esc (leave the chat) does not run as well.
	e.preventDefault();
}
window.addEventListener("keydown", onKey);
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
