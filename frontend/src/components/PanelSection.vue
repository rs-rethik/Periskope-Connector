<template>
	<section>
		<div class="mb-1 flex items-center gap-1">
			<button
				class="-ml-1 flex items-center gap-1 rounded px-1 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink-gray-5 transition hover:bg-surface-gray-2 hover:text-ink-gray-7"
				:aria-expanded="open"
				@click="toggle"
			>
				<LucideChevronRight
					class="size-3 transition-transform"
					:class="open ? 'rotate-90' : ''"
				/>
				{{ title }}
			</button>
			<div class="ml-auto"><slot name="actions" /></div>
		</div>
		<div v-show="open"><slot /></div>
	</section>
</template>

<script setup>
import { ref } from "vue";
import LucideChevronRight from "~icons/lucide/chevron-right";

// A section of the CRM panel that folds; each section remembers its state in this browser.
const props = defineProps({
	id: { type: String, required: true },
	title: { type: String, required: true },
});

const key = `periskope-panel-${props.id}`;
function stored() {
	try {
		return localStorage.getItem(key) !== "closed";
	} catch {
		return true;
	}
}
const open = ref(stored());

function toggle() {
	open.value = !open.value;
	try {
		localStorage.setItem(key, open.value ? "open" : "closed");
	} catch {
		// private window: the choice lasts for this visit only
	}
}
</script>
