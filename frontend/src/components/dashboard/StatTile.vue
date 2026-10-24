<template>
	<button
		class="flex flex-col rounded-lg border border-outline-gray-1 bg-surface-base px-4 py-3 text-left"
		:class="to ? 'hover:border-outline-gray-3' : 'cursor-default'"
		@click="to && $emit('open')"
	>
		<span class="flex items-center gap-1.5 text-xs text-ink-gray-5">
			<component
				:is="icon"
				v-if="icon"
				class="size-3.5"
				:class="tone ? toneClass[tone] : ''"
			/>{{ label }}
		</span>
		<span class="mt-1 text-2xl font-semibold text-ink-gray-9">{{ value ?? "—" }}</span>
		<span v-if="hint" class="mt-0.5 text-xs text-ink-gray-5">{{ hint }}</span>
	</button>
</template>

<script setup>
defineProps({
	label: { type: String, required: true },
	value: { type: [String, Number], default: null },
	hint: { type: String, default: "" },
	icon: { type: [Object, Function], default: null },
	tone: { type: String, default: "" },
	to: { type: Boolean, default: false },
});
defineEmits(["open"]);
// Status tones come with an icon and a label, never colour alone.
const toneClass = {
	critical: "text-ink-red-8",
	warning: "text-ink-amber-8",
	good: "text-ink-green-6",
};
</script>
