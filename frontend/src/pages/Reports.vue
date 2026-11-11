<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="page-head">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Reports</h1>
			<span class="text-sm text-ink-gray-5 max-md:hidden"
				>The app's reports, without opening Desk.</span
			>
		</div>
		<div
			class="flex min-h-0 flex-1 gap-4 overflow-hidden px-5 pb-5 max-md:flex-col max-md:px-3"
		>
			<!-- the reports -->
			<nav
				class="flex shrink-0 flex-col gap-1 md:w-64 max-md:flex-row max-md:overflow-x-auto"
			>
				<button
					v-for="r in list"
					:key="r.name"
					class="rounded-lg px-3 py-2 text-left max-md:shrink-0"
					:class="
						picked?.name === r.name
							? 'bg-surface-base shadow-sm'
							: 'hover:bg-surface-gray-2'
					"
					@click="pick(r)"
				>
					<div class="text-sm font-medium text-ink-gray-9">{{ r.title }}</div>
					<div class="text-xs text-ink-gray-5 max-md:hidden">{{ r.description }}</div>
				</button>
				<p v-if="!list.length && !loading" class="text-sm text-ink-gray-5">
					No report is open to you.
				</p>
			</nav>

			<!-- the chosen report -->
			<section
				v-if="picked"
				class="flex min-h-0 min-w-0 flex-1 flex-col rounded-lg border border-outline-gray-1 bg-surface-base"
			>
				<div class="flex flex-wrap items-end gap-2 border-b border-outline-gray-1 p-3">
					<template v-if="picked.has_period">
						<FormControl v-model="from" type="date" label="From" />
						<FormControl v-model="to" type="date" label="To" />
					</template>
					<Button label="Run" variant="solid" :loading="running" @click="runReport" />
					<Button
						class="ml-auto"
						label="Download CSV"
						:disabled="!rows.length"
						@click="downloadCsv"
					>
						<template #prefix><LucideDownload class="size-4" /></template>
					</Button>
				</div>
				<div class="min-h-0 flex-1 overflow-auto">
					<table v-if="rows.length" class="w-full text-sm">
						<thead
							class="sticky top-0 bg-surface-gray-1 text-left text-xs text-ink-gray-6"
						>
							<tr>
								<th
									v-for="c in columns"
									:key="c.fieldname"
									class="whitespace-nowrap px-3 py-2 font-medium"
									:class="numeric(c) ? 'text-right' : ''"
								>
									{{ c.label }}
								</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="(row, i) in rows"
								:key="i"
								class="border-t border-outline-gray-1"
							>
								<td
									v-for="c in columns"
									:key="c.fieldname"
									class="whitespace-nowrap px-3 py-1.5 text-ink-gray-8"
									:class="numeric(c) ? 'text-right tabular-nums' : ''"
								>
									{{ show(row, c) }}
								</td>
							</tr>
						</tbody>
					</table>
					<p v-else-if="!running" class="p-6 text-center text-sm text-ink-gray-5">
						{{ ran ? "Nothing in this period." : "Choose the dates and press Run." }}
					</p>
					<div v-else class="flex justify-center p-6">
						<LoadingIndicator class="size-5" />
					</div>
					<p v-if="more" class="p-3 text-xs text-ink-gray-5">
						Only the first 2,000 rows are shown and downloaded.
					</p>
				</div>
			</section>
		</div>
	</div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import dayjs from "dayjs";
import { Button, FormControl, LoadingIndicator, call } from "frappe-ui";
import LucideDownload from "~icons/lucide/download";
import { showError } from "@/utils/errors";

const API = "periskope_connector.api.reports.";
const NUMERIC = ["Int", "Float", "Currency", "Percent", "Duration"];
const list = ref([]);
const picked = ref(null);
const loading = ref(false);
const running = ref(false);
const ran = ref(false);
const columns = ref([]);
const rows = ref([]);
const more = ref(false);
const from = ref("");
const to = ref("");

const numeric = (c) => NUMERIC.includes(c.fieldtype);
function value(row, c) {
	return Array.isArray(row) ? row[columns.value.indexOf(c)] : row[c.fieldname];
}
function show(row, c) {
	const v = value(row, c);
	if (v === null || v === undefined || v === "") return "";
	if (c.fieldtype === "Currency" || c.fieldtype === "Float")
		return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
	if (c.fieldtype === "Percent") return `${Number(v).toFixed(1)}%`;
	return String(v);
}

function pick(r) {
	picked.value = r;
	rows.value = [];
	ran.value = false;
	to.value = dayjs().format("YYYY-MM-DD");
	from.value = dayjs()
		.subtract(r.days || 30, "day")
		.format("YYYY-MM-DD");
	runReport();
}

async function runReport() {
	running.value = true;
	try {
		const r = await call(API + "run", {
			report: picked.value.name,
			from_date: picked.value.has_period ? from.value : "",
			to_date: picked.value.has_period ? to.value : "",
		});
		columns.value = r.columns;
		rows.value = r.rows;
		more.value = r.more;
		ran.value = true;
	} catch (e) {
		showError(e, "The report could not be run.");
	} finally {
		running.value = false;
	}
}

function downloadCsv() {
	const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
	const lines = [
		columns.value.map((c) => cell(c.label)).join(","),
		...rows.value.map((row) => columns.value.map((c) => cell(value(row, c))).join(",")),
	];
	const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
	const a = document.createElement("a");
	a.href = URL.createObjectURL(blob);
	a.download = `${picked.value.title.replace(/\s+/g, "_")}_${from.value || "now"}_${
		to.value || ""
	}.csv`;
	a.click();
	URL.revokeObjectURL(a.href);
}

onMounted(async () => {
	loading.value = true;
	try {
		list.value = await call(API + "reports");
		if (list.value.length) pick(list.value[0]);
	} catch (e) {
		showError(e, "The reports could not be listed.");
	} finally {
		loading.value = false;
	}
});
</script>
