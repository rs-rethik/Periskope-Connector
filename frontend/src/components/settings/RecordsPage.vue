<template>
	<div class="flex h-full min-h-0 flex-col">
		<!-- title and its buttons on one line, the description below at full width -->
		<div class="border-b border-outline-gray-1 px-6 py-4">
			<div class="flex flex-wrap items-center gap-2">
				<h2 class="mr-auto text-lg font-semibold text-ink-gray-9">{{ title }}</h2>
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
			<p v-if="description" class="mt-1 text-sm text-ink-gray-5">{{ description }}</p>
		</div>

		<!-- One record -->
		<div v-if="editing" class="min-h-0 flex-1 overflow-y-auto px-6 py-4">
			<button
				class="mb-3 flex items-center gap-1 text-sm text-ink-gray-6 hover:text-ink-gray-9"
				@click="leave"
			>
				<LucideArrowLeft class="size-4" /> All {{ listName }}
			</button>
			<div class="grid max-w-2xl gap-4">
				<FieldInput
					v-for="f in meta.fields"
					:key="f.fieldname"
					v-model="form[f.fieldname]"
					:field="editing.name && f.fieldname === nameField ? { ...f, read_only: 1 } : f"
				/>
			</div>
		</div>
		<!-- always in view while a record is open -->
		<div
			v-if="editing"
			class="flex items-center gap-2 border-t border-outline-gray-1 bg-surface-base px-6 py-3"
		>
			<Button variant="solid" label="Save" :loading="saving" @click="save" />
			<Button label="Cancel" @click="leave" />
			<span v-if="dirty" class="text-xs text-ink-gray-6">Unsaved changes</span>
			<ErrorMessage class="ml-2 min-w-0 truncate" :message="error" />
			<Button
				v-if="editing.name && meta.can_delete"
				class="ml-auto"
				theme="red"
				variant="subtle"
				label="Delete"
				:loading="deleting"
				@click="remove"
			/>
		</div>

		<!-- The list -->
		<div v-else class="min-h-0 flex-1 overflow-auto px-6 py-2">
			<!-- a page can draw its own list (cards), with the same rows and editor -->
			<slot v-if="rows.length && $slots.list" name="list" :rows="rows" :edit="edit" />
			<table v-else-if="rows.length" class="w-full text-sm">
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
			<div
				v-else-if="loadError"
				class="flex items-center justify-center gap-2 py-10 text-sm text-ink-gray-6"
			>
				This list could not be loaded.
				<Button size="sm" label="Retry" @click="load" />
			</div>
			<div v-else-if="!loading" class="py-10 text-center text-sm text-ink-gray-5">
				{{ empty }}
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Button, ErrorMessage, call, toast } from "frappe-ui";
import LucidePlus from "~icons/lucide/plus";
import LucideArrowLeft from "~icons/lucide/arrow-left";
import LucideCheck from "~icons/lucide/check";
import FieldInput from "@/components/settings/FieldInput.vue";
import { fromNow } from "@/utils/format";
import { ask, discardChanges } from "@/utils/confirm";
import { errorText } from "@/utils/errors";

const props = defineProps({
	kind: { type: String, required: true },
	title: { type: String, required: true },
	singular: { type: String, required: true },
	description: { type: String, default: "" },
	empty: { type: String, default: "Nothing here yet." },
	nameField: { type: String, default: "" },
});
const emit = defineEmits(["saved", "dirty"]);

const meta = ref(null);
const rows = ref([]);
const loading = ref(false);
const editing = ref(null);
const form = ref({});
const error = ref("");
const saving = ref(false);
const deleting = ref(false);
const loadError = ref(false);
const original = ref(""); // the record as opened, to tell when it has unsaved changes

// "WhatsApp numbers" stays as it is; "Teams" reads "teams" in "All teams".
const listName = computed(() =>
	/^[A-Z][a-z]/.test(props.title)
		? props.title[0].toLowerCase() + props.title.slice(1)
		: props.title
);

const dirty = computed(() => !!editing.value && JSON.stringify(form.value) !== original.value);
watch(dirty, (d) => emit("dirty", d));
onBeforeUnmount(() => emit("dirty", false));

// Back to the list; unsaved changes are only dropped when the user says so.
async function leave() {
	if (dirty.value && !(await discardChanges())) return;
	editing.value = null;
}

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
		loadError.value = false;
	} catch (e) {
		loadError.value = true;
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
	original.value = JSON.stringify(values);
}

async function save() {
	if (saving.value) return;
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
		error.value = errorText(e, "Not saved.");
	} finally {
		saving.value = false;
	}
}

async function remove() {
	if (deleting.value) return;
	const ok = await ask("This cannot be undone.", {
		title: `Delete this ${props.singular.toLowerCase()}?`,
		confirmLabel: "Delete",
		danger: true,
	});
	if (!ok) return;
	deleting.value = true;
	try {
		await call("periskope_connector.api.admin.delete_record", {
			kind: props.kind,
			name: editing.value.name,
		});
		editing.value = null;
		toast.success("Deleted");
		await load();
		emit("saved");
	} catch (e) {
		error.value = errorText(e, "Not deleted.");
	} finally {
		deleting.value = false;
	}
}

onMounted(load);
defineExpose({ reload: load, edit });
</script>
