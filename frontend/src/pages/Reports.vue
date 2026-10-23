<template>
	<div class="flex h-full min-h-0">
		<nav
			class="w-56 shrink-0 overflow-y-auto border-r border-outline-gray-1 bg-surface-gray-1 p-2"
		>
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
			<div
				v-if="current"
				class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-3"
			>
				<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">{{ current.label }}</h1>
				<div v-for="f in current.filters" :key="f.fieldname" class="w-40">
					<FormControl
						v-model="values[f.fieldname]"
						type="date"
						:placeholder="f.label"
					/>
				</div>
				<Button label="Refresh" :loading="loading" @click="run" />
				<span class="ml-auto text-sm text-ink-gray-5"
					>{{ result?.data?.length || 0 }} rows</span
				>
			</div>
			<div class="min-h-0 flex-1 overflow-auto">
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

function cell(row, c) {
	const v = Array.isArray(row) ? row[result.value.columns.indexOf(c)] : row[c.fieldname];
	if (v == null) return "";
	if (c.fieldtype === "Float" || c.fieldtype === "Percent")
		return Number(v).toFixed(c.fieldtype === "Percent" ? 0 : 1);
	return v;
}

async function run() {
	if (!current.value) return;
	loading.value = true;
	error.value = "";
	try {
		result.value = await call("periskope_connector.api.desk_pages.run_report", {
			name: current.value.name,
			filters: { ...values },
		});
	} catch (e) {
		result.value = null;
		error.value = e?.messages?.[0] || e?.message || "The report could not run.";
	} finally {
		loading.value = false;
	}
}

watch(current, (r) => {
	if (!r) return;
	for (const k of Object.keys(values)) delete values[k];
	for (const f of r.filters)
		values[f.fieldname] = dayjs()
			.add(f.default_days || 0, "day")
			.format("YYYY-MM-DD");
	run();
});
onMounted(async () => {
	list.value = await call("periskope_connector.api.desk_pages.reports");
	if (!route.params.report && list.value.length)
		router.replace({ name: "Reports", params: { report: list.value[0].name } });
});
</script>
