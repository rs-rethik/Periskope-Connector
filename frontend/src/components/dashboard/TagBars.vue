<template>
	<div class="viz-root">
		<div v-if="!rows.length" class="py-6 text-center text-sm text-ink-gray-5">
			No tagged messages in this period.
		</div>
		<Tooltip v-for="r in rows" :key="r.tag" :text="`${r.tag}: ${r.chats} chat(s)`"
			><div
				class="group grid grid-cols-[10rem,1fr,3rem] items-center gap-3 rounded px-1 py-1.5 hover:bg-surface-gray-1"
			>
				<span class="truncate text-sm text-ink-gray-7">{{ r.tag }}</span>
				<span class="h-2 rounded-full bg-[var(--track)]">
					<span
						class="block h-2 rounded-full"
						:style="{
							width: `${(r.chats / max) * 100}%`,
							background: 'var(--series-1)',
						}"
					/>
				</span>
				<span class="text-right text-sm font-medium text-ink-gray-8">{{ r.chats }}</span>
			</div></Tooltip
		>
	</div>
</template>

<script setup>
import { Tooltip } from "frappe-ui";
import { computed } from "vue";

// One series (chats per AI tag), so one hue and no legend: the card title names it.
const props = defineProps({ rows: { type: Array, default: () => [] } });
const max = computed(() => Math.max(1, ...props.rows.map((r) => r.chats)));
</script>
