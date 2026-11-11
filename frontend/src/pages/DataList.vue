<template>
	<div class="flex h-full min-h-0 flex-col">
		<div
			v-if="LOGS.some((l) => l.kind === kind)"
			class="flex gap-1 overflow-x-auto border-b border-outline-gray-1 px-5 pt-3 [scrollbar-width:none]"
		>
			<button
				v-for="l in LOGS"
				:key="l.kind"
				class="shrink-0 rounded-t px-3 py-1.5 text-sm"
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
		<div class="page-head">
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
			<span class="ml-auto whitespace-nowrap text-sm text-ink-gray-5">{{
				total === 1 ? "1 row" : `${total} rows`
			}}</span>
		</div>

		<div
			class="panel page-card min-h-0 flex-1 overflow-auto transition-opacity"
			:class="switching ? 'opacity-60' : ''"
		>
			<!-- a phone: each row as a card (the cells stacked, the title first, the buttons below) -->
			<table class="w-full min-w-max text-sm max-md:block max-md:min-w-0">
				<thead
					class="sticky top-0 z-10 bg-surface-gray-1 text-left text-xs text-ink-gray-5 max-md:hidden"
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
				<tbody class="max-md:block">
					<tr
						v-for="r in rows"
						:key="r.name"
						class="h-11 border-b border-outline-gray-1 hover:bg-surface-gray-1 max-md:flex max-md:h-auto max-md:flex-col max-md:gap-0.5 max-md:px-4 max-md:py-3"
					>
						<Tooltip
							v-for="c in shownColumns"
							:key="c.fieldname"
							:text="String(r[c.fieldname] ?? '')"
							><td
								class="max-w-[24rem] truncate px-4 py-2 text-ink-gray-8 max-md:max-w-none max-md:whitespace-normal max-md:break-words max-md:p-0"
								:class="cardClass(r, c)"
							>
								<span
									v-if="c.fieldname !== titleField(r)"
									class="mr-1 text-ink-gray-5 md:hidden"
									>{{ c.label }}:</span
								>{{ cell(r, c) }}
							</td></Tooltip
						>
						<td
							v-if="hasActions"
							class="row-actions sticky right-0 whitespace-nowrap bg-surface-base px-4 py-1.5 text-right shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.15)] max-md:static max-md:flex max-md:flex-wrap max-md:gap-2 max-md:bg-transparent max-md:p-0 max-md:pt-2 max-md:shadow-none max-md:empty:hidden"
						>
							<template v-if="kind === 'contacts' && r.status === 'New'">
								<Button
									size="sm"
									label="Create lead"
									:loading="busyRow === r.name"
									:disabled="!!busyRow"
									@click="contact(r, 'create_lead')"
								/>
								<Button
									size="sm"
									class="ml-1"
									label="Link"
									:disabled="!!busyRow"
									@click="openLink(r)"
								/>
								<Dropdown
									:options="
										['Personal', 'Vendor', 'Spam', 'Internal'].map((x) => ({
											label: `Ignore: ${x}`,
											onClick: () =>
												contact(r, 'ignore', { reason: `Ignored (${x})` }),
										}))
									"
								>
									<Button
										size="sm"
										class="ml-1"
										label="Ignore"
										:disabled="!!busyRow"
									/>
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
									:loading="busyRow === r.name"
									:disabled="!!busyRow"
									@click="decide(r, 'approve')"
								/>
								<Button
									size="sm"
									class="ml-1"
									label="Reject"
									:disabled="!!busyRow"
									@click="decide(r, 'reject')"
								/>
							</template>
						</td>
					</tr>
				</tbody>
			</table>
			<div
				v-if="loadError && !rows.length"
				class="flex items-center justify-center gap-2 p-10 text-sm text-ink-gray-6"
			>
				This list could not be loaded.
				<Button size="sm" label="Retry" @click="load()" />
			</div>
			<div v-else-if="!rows.length" class="p-10 text-center text-sm text-ink-gray-5">
				{{ loading ? "Loading…" : "Nothing here." }}
			</div>
		</div>

		<div class="-mt-3 flex items-center gap-2 px-6 pb-4">
			<span class="text-sm text-ink-gray-5">{{ rows.length }} of {{ total }}</span>
			<Button
				v-if="rows.length < total"
				class="ml-auto"
				label="Load more"
				:loading="loading"
				:disabled="loading"
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
					<div v-if="target?.kind === 'group'" class="text-sm text-ink-gray-6">
						{{
							currentMapping
								? `Now mapped to ${currentMapping}. Pick another to change it, or save the type only.`
								: "Not mapped to a customer yet."
						}}
					</div>
					<FormControl
						v-if="target?.kind === 'group'"
						v-model="groupType"
						type="select"
						label="Group type"
						:options="['Customer', 'Internal']"
					/>
					<TextInput
						v-model="query"
						:placeholder="
							target?.kind === 'group'
								? 'Search organizations, leads and deals'
								: 'Search leads and deals'
						"
						:debounce="300"
					/>
					<div class="max-h-60 overflow-y-auto">
						<div
							v-if="searching && !matches.length"
							class="p-2 text-sm text-ink-gray-5"
						>
							Searching…
						</div>
						<div v-else-if="!matches.length" class="p-2 text-sm text-ink-gray-5">
							Nothing found. Try a name, company or the last digits of the number.
						</div>
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
									>·
									{{
										m.doctype === "CRM Organization"
											? "Organization"
											: m.doctype === "CRM Deal"
											? "Deal"
											: "Lead"
									}}</span
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
					:loading="saving"
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
	Tooltip,
	call,
	toast,
} from "frappe-ui";
import LucideSearch from "~icons/lucide/search";
import { errorText, showError } from "@/utils/errors";
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
const loadError = ref(false);
const switching = ref(false); // filters changed: dim the old rows until the new ones come
const busyRow = ref(""); // the row whose action is running (one at a time, no double clicks)
const saving = ref(false);
const searching = ref(false);
let latest = 0;
let searchSeq = 0;

const currentMapping = computed(() => {
	const r = target.value?.kind === "group" ? target.value.row : null;
	if (!r) return "";
	if (r.crm_deal) return `deal ${r.crm_deal}`;
	if (r.crm_lead) return `lead ${r.crm_lead}`;
	if (r.organization) return r.organization;
	return "";
});

const shownColumns = computed(() =>
	(meta.value?.columns || []).filter((c) => c.fieldname !== "name" || kind.value === "groups")
);
const hasActions = computed(() => ["contacts", "groups", "suggestions"].includes(kind.value));

function cell(r, c) {
	const value = r[c.fieldname];
	if (c.fieldtype === "Check") return value ? "Yes" : "No";
	if (c.fieldtype === "Datetime") return value ? fromNow(value) : "";
	return value;
}

// A phone's card leads with what people know the row by: the group's or the contact's name (its
// number when it has none), a suggestion's or a log's summary.
const TITLE_FIELDS = ["group_name", "display_name", "phone", "summary"];
function titleField(r) {
	return TITLE_FIELDS.find((f) => r[f] && shownColumns.value.some((c) => c.fieldname === f));
}
function cardClass(r, c) {
	if (c.fieldname === titleField(r))
		return "max-md:order-first max-md:text-base max-md:font-medium max-md:text-ink-gray-9";
	const value = cell(r, c);
	return value === "" || value == null ? "max-md:hidden" : "";
}

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

// A reload after an action keeps every row already loaded, so the list does not jump to the top.
async function load(append = false) {
	const ticket = ++latest;
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
			page_length: append || switching.value ? 0 : rows.value.length,
		});
		if (ticket !== latest) return;
		rows.value = append ? [...rows.value, ...r.rows] : r.rows;
		total.value = r.total;
		loadError.value = false;
	} catch (e) {
		if (ticket !== latest) return;
		if (rows.value.length) fail(e);
		else loadError.value = true;
	} finally {
		if (ticket === latest) loading.value = switching.value = false;
	}
}

function fail(e) {
	showError(e);
}

const DONE = { create_lead: "Lead created", ignore: "Ignored", link: "Linked" };

async function contact(r, action, extra = {}) {
	if (busyRow.value) return;
	busyRow.value = r.name;
	try {
		await call("periskope_connector.api.desk_pages.contact_action", {
			contact: r.name,
			action,
			...extra,
		});
		toast.success(DONE[action] || "Saved");
		await load();
	} catch (e) {
		fail(e);
	} finally {
		busyRow.value = "";
	}
}

async function decide(r, decision) {
	if (busyRow.value) return;
	busyRow.value = r.name;
	try {
		await call("periskope_connector.api.desk_pages.decide_suggestion", {
			name: r.name,
			decision,
		});
		toast.success(decision === "approve" ? "Approved" : "Rejected");
		await load();
	} catch (e) {
		fail(e);
	} finally {
		busyRow.value = "";
	}
}

async function findMatches() {
	const seq = ++searchSeq;
	searching.value = true;
	try {
		const records = call("periskope_connector.api.crm_panel.search_records", {
			query: query.value,
		});
		// a group can also be mapped to a company: organizations first
		const orgs =
			target.value?.kind === "group"
				? call("periskope_connector.api.crm_panel.search_organizations", {
						query: query.value,
				  })
				: Promise.resolve([]);
		const [found, companies] = await Promise.all([records, orgs]);
		const out = [
			...companies.map((o) => ({
				doctype: "CRM Organization",
				name: o.name,
				title: o.title,
				subtitle: `${o.deals} ${o.deals === 1 ? "deal" : "deals"} · ${o.groups} WhatsApp ${
					o.groups === 1 ? "group" : "groups"
				}${o.territory ? ` · ${o.territory}` : ""}`,
			})),
			...found,
		];
		if (seq === searchSeq) matches.value = out;
	} catch (e) {
		if (seq === searchSeq) error.value = errorText(e, "Search failed.");
	} finally {
		if (seq === searchSeq) searching.value = false;
	}
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
	if (saving.value) return;
	saving.value = true;
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
				organization:
					picked.value?.doctype === "CRM Organization" ? picked.value.name : null,
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
		toast.success(target.value.kind === "group" ? "Group saved" : "Linked");
		load();
	} catch (e) {
		error.value = errorText(e, "Not saved.");
	} finally {
		saving.value = false;
	}
}

watch(query, () => linkOpen.value && findMatches());
watch([search, () => JSON.stringify(filters)], () => {
	if (!meta.value) return;
	switching.value = true;
	load();
});
watch(
	kind,
	() => {
		meta.value = null;
		rows.value = [];
		loadError.value = false;
		for (const k of Object.keys(filters)) delete filters[k];
		search.value = "";
		load();
	},
	{ immediate: true }
);
</script>
