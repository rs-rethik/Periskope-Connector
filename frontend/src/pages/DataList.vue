<template>
	<div class="flex h-full min-h-0 flex-col">
		<div
			v-if="LOGS.some((l) => l.kind === kind)"
			class="flex gap-1 border-b border-outline-gray-1 px-5 pt-3"
		>
			<button
				v-for="l in LOGS"
				:key="l.kind"
				class="rounded-t px-3 py-1.5 text-sm"
				:class="
					l.kind === kind
						? 'bg-surface-gray-2 text-ink-gray-9'
						: 'text-ink-gray-6 hover:text-ink-gray-8'
				"
				@click="$router.push({ name: 'DataList', params: { kind: l.kind } })"
			>
				{{ l.label }}
			</button>
		</div>
		<div class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-3">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">{{ meta?.title || "" }}</h1>
			<TextInput
				v-if="meta?.searchable"
				v-model="search"
				class="w-56"
				placeholder="Search"
				:debounce="300"
			>
				<template #prefix><LucideSearch class="size-4 text-ink-gray-5" /></template>
			</TextInput>
			<Select
				v-for="f in meta?.filters || []"
				:key="f.fieldname"
				v-model="filters[f.fieldname]"
				class="w-44"
				:options="filterOptions(f)"
			/>
			<span class="ml-auto text-sm text-ink-gray-5">{{ total }} rows</span>
		</div>

		<div class="min-h-0 flex-1 overflow-auto">
			<table class="w-full min-w-max text-sm">
				<thead
					class="sticky top-0 z-10 bg-surface-gray-1 text-left text-xs text-ink-gray-5"
				>
					<tr>
						<th
							v-for="c in shownColumns"
							:key="c.fieldname"
							class="whitespace-nowrap px-4 py-2 font-medium"
						>
							{{ c.label }}
						</th>
						<th v-if="hasActions" class="sticky right-0 bg-surface-gray-1 px-4 py-2" />
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="r in rows"
						:key="r.name"
						class="border-b border-outline-gray-1 hover:bg-surface-gray-1"
					>
						<td
							v-for="c in shownColumns"
							:key="c.fieldname"
							class="max-w-[24rem] truncate px-4 py-2 text-ink-gray-8"
							:title="String(r[c.fieldname] ?? '')"
						>
							<template v-if="c.fieldtype === 'Check'">{{
								r[c.fieldname] ? "Yes" : "No"
							}}</template>
							<template v-else-if="c.fieldtype === 'Datetime'">{{
								r[c.fieldname] ? fromNow(r[c.fieldname]) : ""
							}}</template>
							<template v-else>{{ r[c.fieldname] }}</template>
						</td>
						<td
							v-if="hasActions"
							class="sticky right-0 whitespace-nowrap bg-surface-base px-4 py-1.5 text-right shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.15)]"
						>
							<template v-if="kind === 'contacts' && r.status === 'New'">
								<Button
									size="sm"
									label="Create lead"
									@click="contact(r, 'create_lead')"
								/>
								<Button size="sm" class="ml-1" label="Link" @click="openLink(r)" />
								<Dropdown
									:options="
										['Personal', 'Vendor', 'Spam', 'Internal'].map((x) => ({
											label: `Ignore: ${x}`,
											onClick: () =>
												contact(r, 'ignore', { reason: `Ignored (${x})` }),
										}))
									"
								>
									<Button size="sm" class="ml-1" label="Ignore" />
								</Dropdown>
							</template>
							<Button
								v-else-if="kind === 'groups'"
								size="sm"
								label="Map"
								@click="openGroup(r)"
							/>
							<template v-else-if="kind === 'suggestions' && r.status === 'Pending'">
								<Button
									size="sm"
									variant="solid"
									label="Approve"
									@click="decide(r, 'approve')"
								/>
								<Button
									size="sm"
									class="ml-1"
									label="Reject"
									@click="decide(r, 'reject')"
								/>
							</template>
						</td>
					</tr>
				</tbody>
			</table>
			<div v-if="!rows.length" class="p-10 text-center text-sm text-ink-gray-5">
				{{ loading ? "Loading…" : "Nothing here." }}
			</div>
		</div>

		<div class="flex items-center gap-2 border-t border-outline-gray-1 px-5 py-2">
			<span class="text-sm text-ink-gray-5">{{ rows.length }} of {{ total }}</span>
			<Button
				v-if="rows.length < total"
				class="ml-auto"
				label="Load more"
				:loading="loading"
				@click="load(true)"
			/>
		</div>

		<!-- Link a contact to a lead or deal / map a group -->
		<Dialog
			v-model="linkOpen"
			:options="{
				title: target?.kind === 'group' ? 'Map the group' : 'Link to a lead or deal',
			}"
		>
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl
						v-if="target?.kind === 'group'"
						v-model="groupType"
						type="select"
						label="Group type"
						:options="['Customer', 'Internal']"
					/>
					<TextInput
						v-model="query"
						placeholder="Search leads and deals"
						:debounce="300"
					/>
					<div class="max-h-60 overflow-y-auto">
						<button
							v-for="m in matches"
							:key="m.doctype + m.name"
							class="flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
							:class="picked?.name === m.name ? 'bg-surface-gray-2' : ''"
							@click="picked = m"
						>
							<span class="text-sm text-ink-gray-8"
								>{{ m.title }}
								<span class="text-xs text-ink-gray-5"
									>· {{ m.doctype === "CRM Deal" ? "Deal" : "Lead" }}</span
								></span
							>
							<span class="truncate text-xs text-ink-gray-5">{{ m.subtitle }}</span>
						</button>
					</div>
					<ErrorMessage :message="error" />
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					:label="target?.kind === 'group' ? 'Save' : 'Link'"
					:disabled="target?.kind !== 'group' && !picked"
					@click="saveLink"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
	Button,
	Dialog,
	Dropdown,
	ErrorMessage,
	FormControl,
	Select,
	TextInput,
	call,
	toast,
} from "frappe-ui";
import LucideSearch from "~icons/lucide/search";
import { fromNow } from "@/utils/format";

const route = useRoute();
const LOGS = [
	{ kind: "agent_actions", label: "What the agent did" },
	{ kind: "auto_replies", label: "Auto replies" },
	{ kind: "reminders", label: "Reminders" },
	{ kind: "agent_runs", label: "Agent runs" },
	{ kind: "ai_usage", label: "AI usage" },
	{ kind: "webhook_logs", label: "Webhook logs" },
];
const kind = computed(() => route.params.kind);
const meta = ref(null);
const rows = ref([]);
const total = ref(0);
const loading = ref(false);
const search = ref("");
const filters = reactive({});
const linkOpen = ref(false);
const target = ref(null);
const query = ref("");
const matches = ref([]);
const picked = ref(null);
const groupType = ref("Customer");
const error = ref("");

const shownColumns = computed(() =>
	(meta.value?.columns || []).filter((c) => c.fieldname !== "name" || kind.value === "groups")
);
const hasActions = computed(() => ["contacts", "groups", "suggestions"].includes(kind.value));

function filterOptions(f) {
	const all = [{ label: `Any ${f.label.toLowerCase()}`, value: "" }];
	if (f.fieldtype === "Check")
		return [...all, { label: "Yes", value: "1" }, { label: "No", value: "0" }];
	if (f.fieldtype === "Select")
		return [
			...all,
			...(f.options || "")
				.split("\n")
				.filter(Boolean)
				.map((o) => ({ label: o, value: o })),
		];
	if (f.fieldtype === "Int")
		return [...all, ...[0, 1, 2, 3].map((n) => ({ label: String(n), value: String(n) }))];
	return all;
}

async function load(append = false) {
	loading.value = true;
	try {
		if (!meta.value) {
			meta.value = await call("periskope_connector.api.desk_pages.list_meta", {
				kind: kind.value,
			});
			for (const f of meta.value.filters)
				if (!(f.fieldname in filters)) filters[f.fieldname] = "";
		}
		const r = await call("periskope_connector.api.desk_pages.list_rows", {
			kind: kind.value,
			filters: { ...filters },
			search: search.value,
			start: append ? rows.value.length : 0,
		});
		rows.value = append ? [...rows.value, ...r.rows] : r.rows;
		total.value = r.total;
	} finally {
		loading.value = false;
	}
}

function fail(e) {
	toast.error(e?.messages?.[0] || e?.message || "That did not work.");
}

async function contact(r, action, extra = {}) {
	try {
		await call("periskope_connector.api.desk_pages.contact_action", {
			contact: r.name,
			action,
			...extra,
		});
		toast.success("Done");
		load();
	} catch (e) {
		fail(e);
	}
}

async function decide(r, decision) {
	try {
		await call("periskope_connector.api.desk_pages.decide_suggestion", {
			name: r.name,
			decision,
		});
		toast.success(decision === "approve" ? "Approved" : "Rejected");
		load();
	} catch (e) {
		fail(e);
	}
}

async function findMatches() {
	matches.value = await call("periskope_connector.api.crm_panel.search_records", {
		query: query.value,
	});
}

function openLink(r) {
	target.value = { kind: "contact", row: r };
	query.value = r.display_name || r.phone || "";
	picked.value = null;
	error.value = "";
	linkOpen.value = true;
	findMatches();
}

function openGroup(r) {
	target.value = { kind: "group", row: r };
	groupType.value = r.group_type || "Customer";
	query.value = r.group_name || "";
	picked.value = null;
	error.value = "";
	linkOpen.value = true;
	findMatches();
}

async function saveLink() {
	error.value = "";
	const lead = picked.value?.doctype === "CRM Lead" ? picked.value.name : null;
	const deal = picked.value?.doctype === "CRM Deal" ? picked.value.name : null;
	try {
		if (target.value.kind === "group") {
			await call("periskope_connector.api.desk_pages.map_group", {
				group: target.value.row.name,
				group_type: groupType.value,
				crm_lead: lead,
				crm_deal: deal,
			});
		} else {
			await call("periskope_connector.api.desk_pages.contact_action", {
				contact: target.value.row.name,
				action: "link",
				lead,
				deal,
			});
		}
		linkOpen.value = false;
		toast.success("Saved");
		load();
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "Not saved.";
	}
}

watch(query, () => linkOpen.value && findMatches());
watch([search, () => JSON.stringify(filters)], () => meta.value && load());
watch(
	kind,
	() => {
		meta.value = null;
		rows.value = [];
		for (const k of Object.keys(filters)) delete filters[k];
		search.value = "";
		load();
	},
	{ immediate: true }
);
</script>
