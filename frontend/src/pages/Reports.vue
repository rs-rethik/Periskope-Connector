<template>
	<div class="flex h-full min-h-0">
		<nav class="panel my-5 ml-5 w-56 shrink-0 overflow-y-auto p-2">
			<div class="px-2 pb-2 pt-1 text-base font-semibold text-ink-gray-9">Reports</div>
			<button
				v-for="r in list"
				:key="r.name"
				class="block w-full rounded px-2 py-1.5 text-left text-sm"
				:class="
					current?.name === r.name
						? 'bg-surface-base text-ink-gray-9 shadow-sm'
						: 'text-ink-gray-7 hover:bg-surface-gray-2'
				"
				@click="$router.push({ name: 'Reports', params: { report: r.name } })"
			>
				{{ r.label }}
			</button>
		</nav>
		<div class="flex min-w-0 flex-1 flex-col">
			<div v-if="current" class="page-head">
				<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">{{ current.label }}</h1>
				<!-- the field name stays beside each date, so a filled From and To can be told apart -->
				<label
					v-for="f in current.filters"
					:key="f.fieldname"
					class="flex items-center gap-1.5 text-sm text-ink-gray-6"
				>
					{{ f.label }}
					<FormControl v-model="values[f.fieldname]" class="w-36" type="date" />
				</label>
				<Button label="Refresh" :loading="loading" @click="run" />
				<span class="ml-auto whitespace-nowrap text-sm text-ink-gray-5">{{
					loading && !result
						? "Loading…"
						: result?.data?.length === 1
						? "1 row"
						: `${result?.data?.length || 0} rows`
				}}</span>
			</div>
			<div
				class="panel page-card min-h-0 flex-1 overflow-auto transition-opacity"
				:class="loading && result ? 'opacity-60' : ''"
			>
				<div v-if="loading && !result" class="p-10 text-center text-sm text-ink-gray-5">
					Loading…
				</div>
				<table v-if="result" class="w-full min-w-max text-sm">
					<thead
						class="sticky top-0 bg-surface-gray-1 text-left text-xs text-ink-gray-5"
					>
						<tr>
							<th
								v-for="c in result.columns"
								:key="c.fieldname"
								class="whitespace-nowrap px-4 py-2 font-medium"
							>
								{{ c.label }}
							</th>
						</tr>
					</thead>
					<tbody>
						<tr
							v-for="(row, i) in result.data"
							:key="i"
							class="border-b border-outline-gray-1"
						>
							<td
								v-for="c in result.columns"
								:key="c.fieldname"
								class="max-w-[24rem] truncate px-4 py-2 text-ink-gray-8"
								:class="
									['Int', 'Float', 'Currency', 'Percent'].includes(c.fieldtype)
										? 'text-right'
										: ''
								"
							>
								{{ cell(row, c) }}
							</td>
						</tr>
					</tbody>
				</table>
				<div
					v-if="result && !result.data.length"
					class="p-10 text-center text-sm text-ink-gray-5"
				>
					No rows.
				</div>
				<div v-if="error" class="p-10 text-center text-sm text-ink-gray-6">
					{{ error }}
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import dayjs from "dayjs";
import { Button, FormControl, call } from "frappe-ui";

const route = useRoute();
const router = useRouter();
const list = ref([]);
const result = ref(null);
const loading = ref(false);
const error = ref("");
const values = reactive({});
const current = computed(() => list.value.find((r) => r.name === route.params.report) || null);
let latest = 0; // only the newest run fills the table (quick report or date changes)
let timer;
let ranWith = ""; // the filters of the last run, so the date watcher does not run them twice

function cell(row, c) {
	const v = Array.isArray(row) ? row[result.value.columns.indexOf(c)] : row[c.fieldname];
	if (v == null) return "";
	if (c.fieldtype === "Float" || c.fieldtype === "Percent")
		return Number(v).toFixed(c.fieldtype === "Percent" ? 0 : 1);
	return v;
}

async function run() {
	if (!current.value) return;
	const ticket = ++latest;
	ranWith = JSON.stringify(values);
	clearTimeout(timer);
	loading.value = true;
	error.value = "";
	try {
		const out = await call("periskope_connector.api.desk_pages.run_report", {
			name: current.value.name,
			filters: { ...values },
		});
		if (ticket === latest) result.value = out;
	} catch (e) {
		if (ticket !== latest) return;
		result.value = null;
		error.value = e?.messages?.[0] || e?.message || "The report could not run.";
	} finally {
		if (ticket === latest) loading.value = false;
	}
}

// Changing a date runs the report again (after a short pause, so typing a date runs it once).
watch(
	() => JSON.stringify(values),
	(now) => {
		if (now === ranWith) return;
		clearTimeout(timer);
		timer = setTimeout(run, 400);
	}
);

watch(current, (r) => {
	if (!r) return;
	result.value = null; // never show the previous report's table under this one's title
	for (const k of Object.keys(values)) delete values[k];
	for (const f of r.filters)
		values[f.fieldname] = dayjs()
			.add(f.default_days || 0, "day")
			.format("YYYY-MM-DD");
	run();
});
onMounted(async () => {
	try {
		list.value = await call("periskope_connector.api.desk_pages.reports");
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "The reports could not be loaded.";
		return;
	}
	if (!route.params.report && list.value.length)
		router.replace({ name: "Reports", params: { report: list.value[0].name } });
});
</script>
