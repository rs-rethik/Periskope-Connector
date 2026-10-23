<template>
	<div class="relative">
		<div
			class="flex min-h-[32px] flex-wrap items-center gap-1 rounded bg-surface-gray-2 px-2 py-1 focus-within:ring-2 focus-within:ring-outline-gray-3"
		>
			<span
				v-for="v in chosen"
				:key="v"
				class="inline-flex items-center gap-1 rounded bg-surface-white px-1.5 py-0.5 text-sm text-ink-gray-8 shadow-sm"
			>
				{{ labelOf(v) }}
				<button class="text-ink-gray-5 hover:text-ink-gray-8" @click="remove(v)">
					<LucideX class="size-3" />
				</button>
			</span>
			<input
				v-if="multiple || !chosen.length"
				v-model="query"
				class="min-w-[8rem] flex-1 border-0 bg-transparent p-0 text-sm focus:ring-0"
				:placeholder="placeholder"
				@focus="open = true"
				@blur="close"
				@keydown.esc="open = false"
			/>
		</div>
		<div
			v-if="open && options.length"
			class="absolute z-30 mt-1 max-h-60 w-full overflow-y-auto rounded-lg border border-outline-gray-2 bg-surface-white p-1 shadow-lg"
		>
			<button
				v-for="o in options"
				:key="o.value"
				class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-surface-gray-2"
				@mousedown.prevent="add(o)"
			>
				<span class="text-ink-gray-8">{{ o.label || o.value }}</span>
				<span
					v-if="o.label && o.label !== o.value"
					class="truncate text-xs text-ink-gray-5"
					>{{ o.value }}</span
				>
			</button>
		</div>
	</div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { call, debounce } from "frappe-ui";
import LucideX from "~icons/lucide/x";

// A value (Link) or several (Table MultiSelect) picked from one doctype, searched on the server.
const props = defineProps({
	doctype: { type: String, required: true },
	modelValue: { type: [String, Array], default: null },
	multiple: { type: Boolean, default: false },
	placeholder: { type: String, default: "Search" },
});
const emit = defineEmits(["update:modelValue"]);

const query = ref("");
const open = ref(false);
const options = ref([]);
const known = ref({});

const chosen = computed(() =>
	props.multiple ? props.modelValue || [] : props.modelValue ? [props.modelValue] : []
);

function labelOf(v) {
	return known.value[v] || v;
}

const search = debounce(async () => {
	const rows = await call("periskope_connector.api.admin.pick", {
		doctype: props.doctype,
		query: query.value,
	});
	for (const r of rows) known.value[r.value] = r.label || r.value;
	options.value = rows.filter((r) => !chosen.value.includes(r.value));
}, 250);

function add(o) {
	known.value[o.value] = o.label || o.value;
	emit("update:modelValue", props.multiple ? [...chosen.value, o.value] : o.value);
	query.value = "";
	open.value = false; // picked: the list closes so it never covers the form; typing opens it again
}

function remove(v) {
	emit("update:modelValue", props.multiple ? chosen.value.filter((x) => x !== v) : null);
}

function close() {
	setTimeout(() => (open.value = false), 150);
}

watch(query, (q) => {
	if (q) open.value = true;
	search();
});
watch(open, (o) => o && search());
// Show names, not ids, for what is already chosen.
watch(
	() => chosen.value.join(","),
	async () => {
		if (chosen.value.some((v) => !known.value[v])) {
			const rows = await call("periskope_connector.api.admin.pick", {
				doctype: props.doctype,
				query: "",
			});
			for (const r of rows) known.value[r.value] = r.label || r.value;
		}
	},
	{ immediate: true }
);
</script>
