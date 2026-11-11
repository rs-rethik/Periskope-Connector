<template>
	<Teleport to="body">
		<div
			v-if="current"
			class="safe-top safe-bottom fixed inset-0 z-50 flex flex-col bg-black text-white"
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
				<!-- zoom -->
				<div class="flex items-center gap-0.5 rounded-full bg-white/10 px-1">
					<Tooltip text="Zoom out (-)"
						><button
							class="rounded-full p-1.5 hover:bg-white/10 disabled:opacity-40 max-md:p-3"
							aria-label="Zoom out (-)"
							:disabled="scale <= MIN"
							@click="zoomBy(1 / STEP)"
						>
							<LucideZoomOut class="size-4" /></button
					></Tooltip>
					<Tooltip text="Actual size (0)"
						><button
							class="w-12 rounded-full py-1 text-center text-xs tabular-nums hover:bg-white/10 max-md:h-10"
							aria-label="Reset the zoom (0)"
							@click="reset"
						>
							{{ Math.round(scale * 100) }}%
						</button></Tooltip
					>
					<Tooltip text="Zoom in (+)"
						><button
							class="rounded-full p-1.5 hover:bg-white/10 disabled:opacity-40 max-md:p-3"
							aria-label="Zoom in (+)"
							:disabled="scale >= MAX"
							@click="zoomBy(STEP)"
						>
							<LucideZoomIn class="size-4" /></button
					></Tooltip>
				</div>
				<Tooltip text="Download"
					><a
						:href="url(current)"
						:download="current.media_filename || current.name"
						class="rounded-full p-2 hover:bg-white/10 max-md:p-2.5"
						aria-label="Download"
					>
						<LucideDownload class="size-5" /> </a
				></Tooltip>
				<Tooltip text="Close (Esc)"
					><button
						class="rounded-full p-2 hover:bg-white/10 max-md:p-2.5"
						aria-label="Close (Esc)"
						@click="close"
					>
						<LucideX class="size-5" /></button
				></Tooltip>
			</div>

			<div
				ref="stage"
				class="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden px-14"
				@click.self="scale === 1 && close()"
				@wheel.prevent="onWheel"
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
					class="max-h-full max-w-full touch-none select-none object-contain"
					:class="
						scale > 1
							? dragging
								? 'cursor-grabbing'
								: 'cursor-grab'
							: 'cursor-zoom-in'
					"
					:style="{
						transform: `translate(${x}px, ${y}px) scale(${scale})`,
						transition: dragging ? 'none' : 'transform 0.12s ease-out',
					}"
					draggable="false"
					alt=""
					@dblclick="toggleZoom"
					@pointerdown="startDrag"
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

			<!-- the caption keeps to three lines unless opened, so the image keeps the screen -->
			<div v-if="current.body" class="mx-auto w-full max-w-2xl px-4 pt-3 text-center">
				<div
					class="whitespace-pre-wrap break-words text-sm text-white/90"
					:class="captionOpen ? 'max-h-[30vh] overflow-y-auto' : 'line-clamp-3'"
				>
					{{ current.body }}
				</div>
				<button
					v-if="longCaption"
					class="mt-1 text-xs font-medium text-white/60 hover:text-white"
					@click="captionOpen = !captionOpen"
				>
					{{ captionOpen ? "Show less" : "Show more" }}
				</button>
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
					<img :src="thumbUrl(m)" class="size-full object-cover" loading="lazy" alt="" />
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
import LucideZoomIn from "~icons/lucide/zoom-in";
import LucideZoomOut from "~icons/lucide/zoom-out";
import { dayLabel, messageTime } from "@/utils/format";
import { mediaUrl as url, thumbUrl } from "@/utils/media";

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

// Zoom: the wheel zooms towards the pointer, a double-click toggles 2.5x, the buttons and + - 0
// step it; zoomed in, the image is dragged to look around. A new image starts at its fitted size.
const MIN = 1;
const MAX = 6;
const STEP = 1.5;
const stage = ref(null);
const scale = ref(1);
const x = ref(0);
const y = ref(0);
const dragging = ref(false);
const captionOpen = ref(false);
const longCaption = computed(() => {
	const body = current.value?.body || "";
	return body.split("\n").length > 3 || body.length > 240;
});

function reset() {
	scale.value = 1;
	x.value = y.value = 0;
}

// Zoom to `next`, keeping the point (px, py) of the stage (from its centre) where it is.
function zoomTo(next, px = 0, py = 0) {
	const target = Math.min(MAX, Math.max(MIN, next));
	if (target === MIN) return reset();
	const ratio = target / scale.value;
	x.value = px - (px - x.value) * ratio;
	y.value = py - (py - y.value) * ratio;
	scale.value = target;
}

function zoomBy(factor) {
	zoomTo(scale.value * factor);
}

function fromCentre(e) {
	const box = stage.value.getBoundingClientRect();
	return [e.clientX - box.left - box.width / 2, e.clientY - box.top - box.height / 2];
}

function onWheel(e) {
	zoomTo(scale.value * Math.exp(-e.deltaY * 0.0015), ...fromCentre(e));
}

function toggleZoom(e) {
	if (scale.value > 1) reset();
	else zoomTo(2.5, ...fromCentre(e));
}

function startDrag(e) {
	if (scale.value <= 1) return;
	e.preventDefault();
	dragging.value = true;
	const start = { cx: e.clientX, cy: e.clientY, x: x.value, y: y.value };
	const move = (ev) => {
		x.value = start.x + ev.clientX - start.cx;
		y.value = start.y + ev.clientY - start.cy;
	};
	const stop = () => {
		dragging.value = false;
		window.removeEventListener("pointermove", move);
		window.removeEventListener("pointerup", stop);
		window.removeEventListener("pointercancel", stop);
	};
	window.addEventListener("pointermove", move);
	window.addEventListener("pointerup", stop);
	window.addEventListener("pointercancel", stop);
}

watch(current, () => {
	reset();
	captionOpen.value = false;
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
	else if (e.key === "+" || e.key === "=") zoomBy(STEP);
	else if (e.key === "-") zoomBy(1 / STEP);
	else if (e.key === "0") reset();
	else return;
	// Handled here, so the chat's own Esc (leave the chat) does not run as well.
	e.preventDefault();
}
window.addEventListener("keydown", onKey);
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
