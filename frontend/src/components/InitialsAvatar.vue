<template>
	<span
		class="inline-grid shrink-0 select-none place-items-center rounded-full font-semibold"
		:class="[tone, sizes[size]]"
		:title="name"
		>{{ initials(name) }}</span
	>
</template>

<script setup>
import { computed } from "vue";
import { initials } from "@/utils/format";

const props = defineProps({
	name: { type: String, default: "" },
	size: { type: String, default: "md" },
});

// The same name always gets the same soft colour.
const TONES = [
	"bg-surface-green-2 text-ink-green-8",
	"bg-surface-blue-2 text-ink-blue-8",
	"bg-surface-amber-2 text-ink-amber-8",
	"bg-surface-violet-2 text-ink-violet-8",
	"bg-surface-red-2 text-ink-red-8",
	"bg-surface-cyan-2 text-ink-cyan-8",
];
const sizes = {
	xs: "size-5 text-[9px]",
	sm: "size-7 text-[11px]",
	md: "size-10 text-sm",
	lg: "size-12 text-base",
};
const tone = computed(() => {
	let h = 0;
	for (const ch of props.name || "") h = (h * 31 + ch.charCodeAt(0)) >>> 0;
	return TONES[h % TONES.length];
});
</script>
