<template>
	<div class="flex flex-col gap-3">
		<div v-if="loading" class="flex justify-center py-6">
			<LoadingIndicator class="size-5" />
		</div>
		<template v-else>
			<TextInput
				v-model="search"
				placeholder="Search templates"
				:debounce="150"
				class="search-pill"
			>
				<template #prefix><LucideSearch class="size-4 text-ink-gray-5" /></template>
			</TextInput>
			<div class="max-h-56 overflow-y-auto rounded-xl border border-outline-gray-1 p-1">
				<button
					v-for="t in shown"
					:key="t.name"
					class="flex w-full flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left transition hover:bg-surface-gray-1"
					:class="picked?.name === t.name ? 'bg-surface-gray-2' : ''"
					@click="pick(t)"
				>
					<span class="flex w-full items-center gap-2">
						<span class="truncate text-sm font-medium text-ink-gray-9">{{
							t.label
						}}</span>
						<span
							class="ml-auto shrink-0 rounded-full bg-surface-gray-2 px-2 text-xs text-ink-gray-6"
							>{{ t.category?.toLowerCase() }}</span
						>
					</span>
					<span class="line-clamp-1 text-xs text-ink-gray-5">{{ t.body }}</span>
				</button>
				<div v-if="!shown.length" class="p-4 text-center text-sm text-ink-gray-5">
					{{
						templates.length
							? "No template matches."
							: "No approved template without a header file."
					}}
				</div>
			</div>

			<template v-if="picked">
				<div v-if="picked.params" class="grid gap-2 sm:grid-cols-2">
					<FormControl
						v-for="(_, i) in values"
						:key="i"
						v-model="values[i]"
						:label="`Value {{${i + 1}}}`"
					/>
				</div>
				<div
					class="whitespace-pre-wrap rounded-xl bg-surface-green-1 px-3 py-2 text-sm text-ink-gray-8"
				>
					{{ preview }}
				</div>
			</template>
		</template>
	</div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { FormControl, LoadingIndicator, TextInput } from "frappe-ui";
import LucideSearch from "~icons/lucide/search";

// Choose an approved Meta WhatsApp template and fill in a value for each {{n}} of its body, with a
// preview of what the customer gets. The choice is the v-model: { template, params, ready }.
const props = defineProps({
	templates: { type: Array, default: () => [] },
	loading: { type: Boolean, default: false },
});
const model = defineModel({ type: Object, default: () => ({}) });

const search = ref("");
const picked = ref(null);
const values = ref([]);

const shown = computed(() => {
	const q = search.value.trim().toLowerCase();
	return q
		? props.templates.filter((t) => `${t.label} ${t.body}`.toLowerCase().includes(q))
		: props.templates;
});
const preview = computed(() =>
	(picked.value?.body || "").replace(
		/\{\{(\d+)\}\}/g,
		(m, n) => values.value[n - 1] || `{{${n}}}`
	)
);

function pick(t) {
	picked.value = t;
	values.value = Array.from({ length: t.params }, () => "");
}

watch(
	[picked, values],
	() => {
		model.value = {
			template: picked.value?.name || null,
			params: [...values.value],
			ready: !!picked.value && values.value.every((v) => (v || "").trim()),
		};
	},
	{ deep: true, immediate: true }
);

// a new list (another number, the dialog opened again) starts with nothing chosen
watch(
	() => props.templates,
	() => {
		picked.value = null;
		search.value = "";
	}
);
</script>
