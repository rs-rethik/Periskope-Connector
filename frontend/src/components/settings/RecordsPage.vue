<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="flex items-center gap-2 border-b border-outline-gray-1 px-6 py-4">
			<div class="min-w-0 flex-1">
				<h2 class="text-lg font-semibold text-ink-gray-9">{{ title }}</h2>
				<p class="text-sm text-ink-gray-5">{{ description }}</p>
			</div>
			<slot name="actions" :reload="load" />
			<Button
				v-if="meta?.can_create && !editing"
				variant="solid"
				:label="`New ${singular}`"
				@click="edit(null)"
			>
				<template #prefix><LucidePlus class="size-4" /></template>
			</Button>
		</div>

		<!-- One record -->
		<div v-if="editing" class="min-h-0 flex-1 overflow-y-auto px-6 py-4">
			<button
				class="mb-3 flex items-center gap-1 text-sm text-ink-gray-6 hover:text-ink-gray-9"
				@click="editing = null"
			>
				<LucideArrowLeft class="size-4" /> All {{ title.toLowerCase() }}
			</button>
			<div class="grid max-w-2xl gap-4">
				<FieldInput
					v-for="f in meta.fields"
					:key="f.fieldname"
					v-model="form[f.fieldname]"
					:field="editing.name && f.fieldname === nameField ? { ...f, read_only: 1 } : f"
				/>
			</div>
			<ErrorMessage class="mt-3" :message="error" />
			<div class="mt-5 flex gap-2">
				<Button variant="solid" label="Save" :loading="saving" @click="save" />
				<Button label="Cancel" @click="editing = null" />
				<Button
					v-if="editing.name && meta.can_delete"
					class="ml-auto"
					theme="red"
					variant="subtle"
					label="Delete"
					@click="remove"
				/>
			</div>
		</div>

		<!-- The list -->
		<div v-else class="min-h-0 flex-1 overflow-auto px-6 py-2">
			<table v-if="rows.length" class="w-full text-sm">
				<thead class="text-left text-xs text-ink-gray-5">
					<tr>
						<th
							v-for="c in meta.list"
							:key="c.fieldname"
							class="whitespace-nowrap py-2 pr-4 font-medium"
						>
							{{ c.label }}
						</th>
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="r in rows"
						:key="r.name"
						class="cursor-pointer border-t border-outline-gray-1 hover:bg-surface-gray-1"
						@click="edit(r)"
					>
						<td
							v-for="c in meta.list"
							:key="c.fieldname"
							class="max-w-[20rem] truncate py-2 pr-4 text-ink-gray-8"
						>
							<slot name="cell" :row="r" :field="c">
								<template v-if="c.fieldtype === 'Check'">
									<LucideCheck
										v-if="r[c.fieldname]"
										class="size-4 text-ink-green-6"
									/>
								</template>
								<template v-else>{{ show(r[c.fieldname]) }}</template>
							</slot>
						</td>
					</tr>
				</tbody>
			</table>
			<div v-else-if="!loading" class="py-10 text-center text-sm text-ink-gray-5">
				{{ empty }}
			</div>
		</div>
	</div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { Button, ErrorMessage, call, toast } from "frappe-ui";
import LucidePlus from "~icons/lucide/plus";
import LucideArrowLeft from "~icons/lucide/arrow-left";
import LucideCheck from "~icons/lucide/check";
import FieldInput from "@/components/settings/FieldInput.vue";
import { fromNow } from "@/utils/format";

const props = defineProps({
	kind: { type: String, required: true },
	title: { type: String, required: true },
	singular: { type: String, required: true },
	description: { type: String, default: "" },
	empty: { type: String, default: "Nothing here yet." },
	nameField: { type: String, default: "" },
});
const emit = defineEmits(["saved"]);

const meta = ref(null);
const rows = ref([]);
const loading = ref(false);
const editing = ref(null);
const form = ref({});
const error = ref("");
const saving = ref(false);

function show(v) {
	if (Array.isArray(v)) return v.join(", ");
	if (typeof v === "string" && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(v)) return fromNow(v);
	return v ?? "";
}

async function load() {
	loading.value = true;
	try {
		if (!meta.value)
			meta.value = await call("periskope_connector.api.admin.get_meta", {
				kind: props.kind,
			});
		rows.value = await call("periskope_connector.api.admin.list_records", {
			kind: props.kind,
		});
	} finally {
		loading.value = false;
	}
}

function edit(row) {
	error.value = "";
	editing.value = row || {};
	const values = {};
	for (const f of meta.value.fields) {
		values[f.fieldname] = row
			? row[f.fieldname]
			: f.fieldtype.startsWith("Table")
			? []
			: f.fieldtype === "Check"
			? Number(f.default || 0)
			: f.default ?? null;
	}
	form.value = values;
}

async function save() {
	saving.value = true;
	error.value = "";
	try {
		await call("periskope_connector.api.admin.save_record", {
			kind: props.kind,
			name: editing.value.name || null,
			values: form.value,
		});
		toast.success("Saved");
		editing.value = null;
		await load();
		emit("saved");
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "Not saved.";
	} finally {
		saving.value = false;
	}
}

async function remove() {
	if (!window.confirm(`Delete this ${props.singular.toLowerCase()}?`)) return;
	try {
		await call("periskope_connector.api.admin.delete_record", {
			kind: props.kind,
			name: editing.value.name,
		});
		editing.value = null;
		await load();
		emit("saved");
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "Not deleted.";
	}
}

onMounted(load);
defineExpose({ reload: load, edit });
</script>
