<template>
	<div ref="root" class="relative">
		<TextInput
			v-model="query"
			:placeholder="placeholder"
			:debounce="250"
			@focus="openList"
			@keydown.esc="open = false"
		>
			<template #prefix><LucideSearch class="size-3.5 text-ink-gray-5" /></template>
		</TextInput>
		<div
			v-if="open && (rows.length || loading || query)"
			class="absolute inset-x-0 top-full z-50 mt-1 max-h-56 overflow-y-auto rounded-lg border border-outline-gray-2 bg-surface-base p-1 shadow-lg"
		>
			<div v-if="loading" class="px-2 py-1.5 text-sm text-ink-gray-5">Searching…</div>
			<button
				v-for="r in rows"
				v-else
				:key="r.value"
				class="flex w-full flex-col items-start rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
				@mousedown.prevent="choose(r)"
			>
				<span class="truncate text-sm text-ink-gray-9">{{ r.label || r.value }}</span>
				<span
					v-if="r.label && r.label !== r.value"
					class="truncate text-xs text-ink-gray-5"
					>{{ r.value }}</span
				>
			</button>
			<div v-if="!loading && !rows.length" class="px-2 py-1.5 text-sm text-ink-gray-5">
				Nothing found.
			</div>
		</div>
	</div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { TextInput, call } from "frappe-ui";
import LucideSearch from "~icons/lucide/search";

// Search ERPNext customers or items by name or code (api/sales_order.search) and pick one; emits
// the chosen row ({ value, label, rate?, uom? }) and clears the box.
const props = defineProps({
	doctype: { type: String, required: true },
	placeholder: { type: String, default: "Search" },
	customer: { type: String, default: "" }, // items: priced in this customer's price list
});
const emit = defineEmits(["pick"]);

const root = ref(null);
const query = ref("");
const rows = ref([]);
const open = ref(false);
const loading = ref(false);
let ticket = 0;

async function load() {
	const mine = ++ticket;
	loading.value = true;
	try {
		const found = await call("periskope_connector.api.sales_order.search", {
			doctype: props.doctype,
			query: query.value,
			customer: props.customer || null,
		});
		if (mine === ticket) rows.value = found;
	} catch {
		if (mine === ticket) rows.value = [];
	} finally {
		if (mine === ticket) loading.value = false;
	}
}

function openList() {
	open.value = true;
	if (!rows.value.length) load();
}

let picking = false; // clearing the box after a pick is not a new search
function choose(r) {
	emit("pick", r);
	picking = true;
	query.value = "";
	open.value = false;
}

watch(query, () => {
	if (picking) {
		picking = false;
		rows.value = [];
		return;
	}
	open.value = true;
	load();
});

function outside(e) {
	if (root.value && !root.value.contains(e.target)) open.value = false;
}
onMounted(() => document.addEventListener("mousedown", outside));
onBeforeUnmount(() => document.removeEventListener("mousedown", outside));
</script>
