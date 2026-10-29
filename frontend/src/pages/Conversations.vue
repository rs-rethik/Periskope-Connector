<template>
	<div class="flex h-full min-h-0 flex-col">
		<!-- Toolbar -->
		<div class="page-head">
			<h1 class="mr-1 truncate text-lg font-semibold text-ink-gray-9">{{ title }}</h1>
			<TextInput
				v-model="search"
				class="min-w-[10rem] max-w-56 flex-1"
				placeholder="Search"
				:debounce="300"
			>
				<template #prefix><LucideSearch class="size-4 text-ink-gray-5" /></template>
			</TextInput>
			<Select v-model="status" class="w-44" :options="statusOptions" />
			<Popover placement="bottom-start">
				<template #target="{ togglePopover }">
					<Button
						:label="filterCount ? `Filters · ${filterCount}` : 'Filters'"
						@click="togglePopover()"
					>
						<template #prefix><LucideListFilter class="size-4" /></template>
					</Button>
				</template>
				<template #body>
					<div
						class="mt-1 w-96 rounded-lg border border-outline-gray-2 bg-surface-base p-3 shadow-xl"
					>
						<div v-for="(f, i) in extra" :key="i" class="mb-2 flex items-center gap-2">
							<Select
								v-model="f.field"
								class="w-40"
								:options="
									filterFields.map((x) => ({ label: x.label, value: x.key }))
								"
								@update:model-value="f.value = ''"
							/>
							<Select
								v-if="fieldOf(f.field)?.options"
								v-model="f.value"
								class="flex-1"
								:options="fieldOf(f.field).options()"
							/>
							<TextInput
								v-else
								v-model="f.value"
								class="flex-1"
								placeholder="value"
								:debounce="400"
							/>
							<Button variant="ghost" @click="extra.splice(i, 1)"
								><template #icon><LucideX class="size-4" /></template
							></Button>
						</div>
						<div class="flex gap-2">
							<Button
								size="sm"
								label="Add filter"
								@click="extra.push({ field: 'team', value: '' })"
							/>
							<Button
								v-if="extra.length"
								size="sm"
								variant="ghost"
								label="Clear"
								@click="extra.splice(0)"
							/>
						</div>
					</div>
				</template>
			</Popover>
			<!-- sort, grouping and columns in one place, so the toolbar fits on one line -->
			<Popover placement="bottom-start">
				<template #target="{ togglePopover }">
					<Button label="Display" @click="togglePopover()">
						<template #prefix><LucideSlidersHorizontal class="size-4" /></template>
					</Button>
				</template>
				<template #body>
					<div
						class="mt-1 flex w-72 flex-col gap-3 rounded-lg border border-outline-gray-2 bg-surface-base p-3 shadow-xl"
					>
						<div>
							<div class="mb-1 text-xs text-ink-gray-5">Sort by</div>
							<div class="flex gap-1">
								<Select v-model="sortKey" class="flex-1" :options="sortOptions" />
								<Button
									:title="order === 'desc' ? 'Newest first' : 'Oldest first'"
									@click="order = order === 'desc' ? 'asc' : 'desc'"
								>
									<template #icon>
										<LucideArrowDownWideNarrow
											v-if="order === 'desc'"
											class="size-4"
										/>
										<LucideArrowUpNarrowWide v-else class="size-4" />
									</template>
								</Button>
							</div>
						</div>
						<div>
							<div class="mb-1 text-xs text-ink-gray-5">Group by</div>
							<Select v-model="groupBy" class="w-full" :options="groupOptions" />
						</div>
						<div>
							<div class="mb-1 text-xs text-ink-gray-5">Columns</div>
							<label
								v-for="c in COLUMNS"
								:key="c.key"
								class="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
							>
								<input
									type="checkbox"
									class="rounded border-outline-gray-3"
									:checked="visible.includes(c.key)"
									@change="toggleColumn(c.key)"
								/>
								{{ c.label }}
							</label>
						</div>
					</div>
				</template>
			</Popover>
			<Dropdown :options="viewOptions" placement="right">
				<Button :label="view ? view.label : 'Save view'"
					><template #prefix><LucideBookmark class="size-4" /></template
				></Button>
			</Dropdown>
			<span class="ml-auto whitespace-nowrap text-sm text-ink-gray-5">{{
				plural(total, "chat")
			}}</span>
		</div>

		<!-- Table -->
		<div
			class="panel page-card min-h-0 flex-1 overflow-auto transition-opacity"
			:class="switching ? 'opacity-60' : ''"
		>
			<table class="w-full min-w-max border-collapse text-sm">
				<thead
					class="sticky top-0 z-10 bg-surface-gray-1 text-left text-xs text-ink-gray-5"
				>
					<tr>
						<th class="w-10 px-3 py-2">
							<input
								type="checkbox"
								class="rounded border-outline-gray-3"
								:checked="allSelected"
								@change="toggleAll"
							/>
						</th>
						<th
							v-for="c in shownColumns"
							:key="c.key"
							class="cursor-pointer whitespace-nowrap px-3 py-2 font-medium hover:text-ink-gray-8"
							:style="{ minWidth: c.width }"
							@click="c.sort && setSort(c.sort)"
						>
							{{ c.label }}
							<span v-if="c.sort && sortKey === c.sort">{{
								order === "desc" ? "↓" : "↑"
							}}</span>
						</th>
					</tr>
				</thead>
				<tbody>
					<template v-for="g in groups" :key="g.key">
						<tr v-if="groupBy" class="bg-surface-gray-1">
							<td :colspan="shownColumns.length + 1" class="px-3 py-1.5">
								<button
									class="flex items-center gap-1 text-xs font-medium text-ink-gray-7"
									@click="toggleGroup(g.key)"
								>
									<LucideChevronRight
										class="size-3.5 transition-transform"
										:class="collapsed.has(g.key) ? '' : 'rotate-90'"
									/>
									{{ g.label }}
									<span class="text-ink-gray-5"
										>· {{ g.rows.length
										}}{{ rows.length < total ? " loaded" : "" }}</span
									>
								</button>
							</td>
						</tr>
						<template v-if="!collapsed.has(g.key)">
							<tr
								v-for="row in g.rows"
								:key="row.name"
								class="cursor-pointer border-b border-outline-gray-1 hover:bg-surface-gray-1"
								:class="selected.has(row.name) ? 'bg-surface-gray-2' : ''"
								@click="openRow(row)"
							>
								<td class="px-3 py-2" @click.stop>
									<input
										type="checkbox"
										class="rounded border-outline-gray-3"
										:checked="selected.has(row.name)"
										@change="toggle(row.name)"
									/>
								</td>
								<td
									v-for="c in shownColumns"
									:key="c.key"
									class="max-w-[22rem] truncate px-3 py-2 text-ink-gray-7"
								>
									<div v-if="c.key === 'title'" class="flex items-center gap-2">
										<span
											v-if="row.unread"
											class="size-2 shrink-0 rounded-full bg-surface-green-6"
										/>
										<span
											class="truncate"
											:class="
												row.unread
													? 'font-semibold text-ink-gray-9'
													: 'text-ink-gray-8'
											"
											>{{ row.title }}</span
										>
									</div>
									<Badge
										v-else-if="c.key === 'status'"
										:label="row.status"
										variant="subtle"
										:theme="statusTheme[row.status]"
									/>
									<SlaBadge v-else-if="c.key === 'sla'" :chat="row" />
									<Badge
										v-else-if="c.key === 'ai_tag' && row.ai_tag"
										:label="row.ai_tag"
										variant="subtle"
										theme="blue"
									/>
									<span v-else-if="c.key === 'labels'" class="flex gap-1">
										<Badge
											v-for="l in row.labels"
											:key="l"
											:label="l"
											variant="outline"
										/>
									</span>
									<span v-else-if="c.key === 'record'">
										{{
											row.crm_deal
												? "Deal " + (row.deal_title || row.crm_deal)
												: row.crm_lead
												? "Lead " + (row.lead_title || row.crm_lead)
												: ""
										}}
									</span>
									<span v-else-if="c.key === 'assigned_to'">{{
										(row.assigned_to || []).join(", ")
									}}</span>
									<span
										v-else-if="c.key === 'last_message_at'"
										class="text-ink-gray-6"
										>{{ fromNow(row.last_message_at) }}</span
									>
									<span v-else-if="c.key === 'contact_number'">{{
										row.contact_number
											? "+" + row.contact_number
											: row.is_group
											? "Group"
											: ""
									}}</span>
									<span v-else>{{ row[c.key] }}</span>
								</td>
							</tr>
						</template>
					</template>
				</tbody>
			</table>
			<div
				v-if="loadError"
				class="flex items-center justify-center gap-2 p-10 text-sm text-ink-gray-6"
			>
				The chats could not be loaded.
				<Button size="sm" label="Retry" @click="load()" />
			</div>
			<div v-else-if="!rows.length" class="p-10 text-center text-sm text-ink-gray-5">
				{{ loading ? "Loading…" : "No chats" }}
			</div>
		</div>

		<!-- Footer -->
		<div class="-mt-3 flex items-center gap-2 px-6 pb-4">
			<Select
				v-model="pageLength"
				class="w-24"
				:options="[20, 50, 100, 200].map((n) => ({ label: String(n), value: n }))"
			/>
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

		<!-- Bulk actions -->
		<div
			v-if="selected.size"
			class="fixed bottom-6 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-lg bg-surface-base px-4 py-2 shadow-2xl ring-1 ring-outline-gray-2"
		>
			<span class="mr-2 text-sm text-ink-gray-8">{{ selected.size }} selected</span>
			<LoadingIndicator v-if="bulkBusy" class="size-4" />
			<Button
				size="sm"
				label="Assign to me"
				:disabled="bulkBusy"
				@click="bulk('assign_to_me')"
			/>
			<Dropdown
				:options="
					statusOptions
						.filter((s) => !['active', 'any'].includes(s.value))
						.map((s) => ({
							label: s.label,
							onClick: () => bulk('set_status', { status: s.value }),
						}))
				"
			>
				<Button size="sm" label="Status" :disabled="bulkBusy" />
			</Dropdown>
			<Dropdown
				v-if="boot.is_manager"
				:options="[
					...inbox.teams.map((t) => ({
						label: t.name,
						onClick: () => bulk('set_team', { team: t.name }),
					})),
					{ label: 'No team', onClick: () => bulk('set_team', { team: '' }) },
				]"
			>
				<Button size="sm" label="Team" :disabled="bulkBusy" />
			</Dropdown>
			<Dropdown
				:options="
					boot.tags.map((t) => ({
						label: t,
						onClick: () => bulk('add_label', { tag: t }),
					}))
				"
			>
				<Button size="sm" label="Add label" :disabled="bulkBusy" />
			</Dropdown>
			<Button size="sm" label="Mark read" :disabled="bulkBusy" @click="bulk('mark_read')" />
			<Button size="sm" variant="ghost" @click="selected.clear()"
				><template #icon><LucideX class="size-4" /></template
			></Button>
		</div>

		<Dialog
			v-model="saveDialog"
			:options="{ title: view?.mine ? 'Update view' : 'Save view' }"
		>
			<template #body-content>
				<form class="flex flex-col gap-3" @submit.prevent="saveView">
					<FormControl v-model="viewLabel" label="Name" autofocus />
					<FormControl
						v-if="boot.is_manager"
						v-model="viewShared"
						type="checkbox"
						label="Share with everyone"
					/>
					<div class="text-xs text-ink-gray-5">
						Saves the filters, columns, sort and grouping you see now.
					</div>
					<button type="submit" class="hidden" />
				</form>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Save"
					:loading="viewBusy"
					:disabled="!viewLabel"
					@click="saveView"
				/>
			</template>
		</Dialog>

		<Dialog v-model="deleteDialog" :options="{ title: 'Delete this view?' }">
			<template #body-content>
				<p class="text-sm text-ink-gray-7">
					“{{ view?.label }}” will be removed{{
						view?.is_shared ? " for everyone" : ""
					}}.
				</p>
			</template>
			<template #actions>
				<Button
					variant="solid"
					theme="red"
					label="Delete"
					:loading="viewBusy"
					@click="deleteView"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
	Badge,
	Button,
	Dialog,
	Dropdown,
	FormControl,
	LoadingIndicator,
	Popover,
	Select,
	TextInput,
	call,
	toast,
} from "frappe-ui";
import LucideSearch from "~icons/lucide/search";
import LucideSlidersHorizontal from "~icons/lucide/sliders-horizontal";
import LucideListFilter from "~icons/lucide/list-filter";
import LucideBookmark from "~icons/lucide/bookmark";
import LucideX from "~icons/lucide/x";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucideArrowDownWideNarrow from "~icons/lucide/arrow-down-wide-narrow";
import LucideArrowUpNarrowWide from "~icons/lucide/arrow-up-narrow-wide";
import SlaBadge from "@/components/SlaBadge.vue";
import { boot } from "@/boot";
import { currentFilters, inbox, loadCounts, loadTeams, loadViews } from "@/stores/inbox";
import { fromNow } from "@/utils/format";
import { showError } from "@/utils/errors";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const route = useRoute();
const router = useRouter();

const COLUMNS = [
	{ label: "Contact", key: "title", width: "200px", sort: "chat_name" },
	{ label: "Number", key: "contact_number", width: "140px" },
	{ label: "Our number", key: "number_label", width: "130px" },
	{ label: "Last message", key: "last_message_preview", width: "300px" },
	{ label: "Status", key: "status", width: "100px", sort: "status" },
	{ label: "SLA", key: "sla", width: "110px", sort: "response_due" },
	{ label: "Assigned to", key: "assigned_to", width: "170px" },
	{ label: "Team", key: "team", width: "120px", sort: "team" },
	{ label: "AI tag", key: "ai_tag", width: "140px" },
	{ label: "Labels", key: "labels", width: "140px" },
	{ label: "Lead / Deal", key: "record", width: "190px" },
	{ label: "Last activity", key: "last_message_at", width: "120px", sort: "last_message_at" },
	{ label: "Messages", key: "message_count", width: "90px", sort: "message_count" },
];
const DEFAULT_COLUMNS = COLUMNS.filter(
	(c) => !["message_count", "labels", "team"].includes(c.key)
).map((c) => c.key);
const STORE = "periskope_inbox_columns";

function storedColumns() {
	try {
		const saved = JSON.parse(localStorage.getItem(STORE) || "null");
		if (Array.isArray(saved) && saved.length) return saved;
	} catch (e) {
		// no saved choice
	}
	return DEFAULT_COLUMNS;
}

const visible = ref(storedColumns());
const shownColumns = computed(() => COLUMNS.filter((c) => visible.value.includes(c.key)));
function toggleColumn(key) {
	const keys = visible.value.includes(key)
		? visible.value.filter((k) => k !== key)
		: [...visible.value, key];
	if (!keys.length) return; // keep at least one column
	visible.value = COLUMNS.map((x) => x.key).filter((k) => keys.includes(k));
	try {
		localStorage.setItem(STORE, JSON.stringify(visible.value));
	} catch (e) {
		// private window: the choice lasts for this visit
	}
}

const statusTheme = { Open: "green", Pending: "orange", Resolved: "blue", Closed: "gray" };
const statusOptions = [
	{ label: "Open and pending", value: "active" },
	{ label: "Open", value: "Open" },
	{ label: "Pending", value: "Pending" },
	{ label: "Resolved", value: "Resolved" },
	{ label: "Closed", value: "Closed" },
	{ label: "Any status", value: "any" },
];
const sortOptions = [
	{ label: "Last activity", value: "last_message_at" },
	{ label: "Reply due", value: "response_due" },
	{ label: "Waiting since", value: "waiting_since" },
	{ label: "Contact name", value: "chat_name" },
	{ label: "Status", value: "status" },
	{ label: "Team", value: "team" },
	{ label: "Messages", value: "message_count" },
	{ label: "Created", value: "creation" },
];
const groupOptions = [
	{ label: "No grouping", value: "" },
	{ label: "Group by status", value: "status" },
	{ label: "Group by our number", value: "number_label" },
	{ label: "Group by team", value: "team" },
	{ label: "Group by assignee", value: "assignee" },
	{ label: "Group by AI tag", value: "ai_tag" },
	{ label: "Group by SLA", value: "sla" },
];
const yesNo = () => [
	{ label: "Yes", value: "1" },
	{ label: "No", value: "0" },
];
const filterFields = [
	{
		key: "team",
		label: "Team",
		options: () => inbox.teams.map((t) => ({ label: t.name, value: t.name })),
	},
	{ key: "assigned_to", label: "Assigned to (email)" },
	{ key: "tag", label: "AI tag", options: () => boot.tags.map((t) => ({ label: t, value: t })) },
	{
		key: "label",
		label: "Label",
		options: () => boot.tags.map((t) => ({ label: t, value: t })),
	},
	{
		key: "number",
		label: "Our number",
		options: () => boot.numbers.map((n) => ({ label: n.label, value: n.name })),
	},
	{ key: "linked", label: "In the CRM", options: yesNo },
	{ key: "groups", label: "Group chat", options: yesNo },
	{ key: "unread", label: "Unread", options: () => [{ label: "Yes", value: "1" }] },
	{ key: "unanswered", label: "Unanswered", options: () => [{ label: "Yes", value: "1" }] },
	{ key: "sla", label: "SLA", options: () => [{ label: "Breached", value: "breached" }] },
];
function fieldOf(key) {
	return filterFields.find((f) => f.key === key);
}

const search = ref("");
const status = ref("active");
const sortKey = ref("last_message_at");
const order = ref("desc");
const groupBy = ref("");
const pageLength = ref(50);
const extra = reactive([]);
const rows = ref([]);
const total = ref(0);
const loading = ref(false);
const loadError = ref(false);
const switching = ref(false); // the filters changed: dim the old rows until the new ones arrive
const bulkBusy = ref(false);
const viewBusy = ref(false);
const deleteDialog = ref(false);
let latest = 0; // only the newest request may fill the table
const selected = reactive(new Set());
const collapsed = reactive(new Set());
const view = ref(null);
const saveDialog = ref(false);
const viewLabel = ref("");
const viewShared = ref(false);

const title = computed(() => view.value?.label || "Conversations");
const filterCount = computed(() => extra.filter((f) => f.field && f.value !== "").length);

function filters() {
	const f = currentFilters({ search: search.value });
	delete f.status;
	if (status.value !== "any") f.status = status.value;
	for (const x of extra) if (x.field && x.value !== "") f[x.field] = x.value;
	return f;
}

async function load(append = false) {
	const ticket = ++latest;
	loading.value = true;
	try {
		const r = await call("periskope_connector.api.inbox.get_chats", {
			filters: filters(),
			order_by: sortKey.value,
			order: order.value,
			start: append ? rows.value.length : 0,
			page_length:
				append || switching.value
					? pageLength.value
					: Math.max(pageLength.value, rows.value.length),
		});
		if (ticket !== latest) return;
		rows.value = append ? [...rows.value, ...r.chats] : r.chats;
		total.value = r.total;
		loadError.value = false;
		for (const name of [...selected])
			if (!rows.value.some((x) => x.name === name)) selected.delete(name);
	} catch (e) {
		if (ticket !== latest) return;
		if (rows.value.length) showError(e, "The chats could not be refreshed.");
		else loadError.value = true;
	} finally {
		if (ticket === latest) loading.value = switching.value = false;
	}
}

const groups = computed(() => {
	if (!groupBy.value) return [{ key: "all", label: "", rows: rows.value }];
	const out = new Map();
	for (const row of rows.value) {
		const keys =
			groupBy.value === "assignee"
				? row.assigned_to?.length
					? row.assigned_to
					: ["Unassigned"]
				: [row[groupBy.value] || (groupBy.value === "sla" ? "No SLA" : "None")];
		for (const k of keys) {
			if (!out.has(k)) out.set(k, { key: k, label: k, rows: [] });
			out.get(k).rows.push(row);
		}
	}
	return [...out.values()];
});

function toggleGroup(key) {
	collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key);
}

const allSelected = computed(
	() => rows.value.length > 0 && rows.value.every((r) => selected.has(r.name))
);
function toggle(name) {
	selected.has(name) ? selected.delete(name) : selected.add(name);
}
function toggleAll() {
	if (allSelected.value) selected.clear();
	else rows.value.forEach((r) => selected.add(r.name));
}

function setSort(key) {
	if (sortKey.value === key) order.value = order.value === "desc" ? "asc" : "desc";
	else sortKey.value = key;
}

function openRow(row) {
	router.push({ name: "Conversation", params: { chat: row.name } });
}

async function bulk(method, args = {}) {
	if (bulkBusy.value) return;
	bulkBusy.value = true;
	try {
		const r = await call(`periskope_connector.api.chat_actions.${method}`, {
			chats: [...selected],
			...args,
		});
		toast.success(`${plural(r.updated, "chat")} updated`);
		selected.clear();
		load();
		loadCounts();
	} catch (e) {
		showError(e);
	} finally {
		bulkBusy.value = false;
	}
}

// --- Saved views ---------------------------------------------------------------------------------

const viewOptions = computed(() => [
	{
		label: view.value?.mine ? "Update this view" : "Save as new view",
		onClick: () => openSave(),
	},
	...(view.value?.mine
		? [{ label: "Delete this view", onClick: () => (deleteDialog.value = true) }]
		: []),
	...(view.value
		? [
				{
					label: "Back to all conversations",
					onClick: () => router.push({ name: "Conversations" }),
				},
		  ]
		: []),
]);

function openSave() {
	viewLabel.value = view.value?.mine ? view.value.label : "";
	viewShared.value = !!view.value?.is_shared;
	saveDialog.value = true;
}

async function saveView() {
	if (viewBusy.value || !viewLabel.value) return;
	viewBusy.value = true;
	const f = { ...filters(), quick: inbox.quick };
	delete f.search;
	try {
		const saved = await call("periskope_connector.api.views.save_view", {
			name: view.value?.mine ? view.value.name : null,
			label: viewLabel.value,
			filters: {
				...f,
				status: status.value,
				extra: extra.filter((x) => x.field && x.value !== ""),
			},
			columns: visible.value,
			sort_by: sortKey.value,
			sort_order: order.value,
			group_by: groupBy.value,
			is_shared: viewShared.value ? 1 : 0,
		});
		saveDialog.value = false;
		toast.success("View saved");
		await loadViews();
		router.push({ name: "Conversations", query: { view: saved.name } });
	} catch (e) {
		showError(e, "The view was not saved.");
	} finally {
		viewBusy.value = false;
	}
}

async function deleteView() {
	if (viewBusy.value) return;
	viewBusy.value = true;
	try {
		await call("periskope_connector.api.views.delete_view", { name: view.value.name });
		deleteDialog.value = false;
		toast.success("View deleted");
		await loadViews();
		router.push({ name: "Conversations" });
	} catch (e) {
		showError(e, "The view was not deleted.");
	} finally {
		viewBusy.value = false;
	}
}

// Leaving a view goes back to the plain list, not the last view's filters under a new title.
function applyView(v) {
	const was = view.value;
	view.value = v;
	if (!v) {
		if (was) {
			status.value = "active";
			extra.splice(0);
			visible.value = storedColumns();
			sortKey.value = "last_message_at";
			order.value = "desc";
			groupBy.value = "";
		}
		return;
	}
	const f = v.filters || {};
	status.value = f.status || "active";
	if (f.quick) inbox.quick = f.quick;
	inbox.number = f.number || "";
	extra.splice(0, extra.length, ...(f.extra || []));
	if (v.columns?.length) visible.value = v.columns;
	sortKey.value = v.sort_by || "last_message_at";
	order.value = v.sort_order || "desc";
	groupBy.value = v.group_by || "";
}

async function syncView() {
	if (!route.query.view) return applyView(null);
	if (!inbox.views.length) await loadViews();
	applyView(inbox.views.find((v) => v.name === route.query.view) || null);
}

watch(
	() => [
		inbox.quick,
		inbox.number,
		search.value,
		status.value,
		sortKey.value,
		order.value,
		pageLength.value,
		// only complete filter rows change the result; a half-filled row does not reload
		JSON.stringify(extra.filter((x) => x.field && x.value !== "")),
	],
	() => {
		switching.value = true;
		load();
	}
);
watch(
	() => inbox.version,
	() => load()
);
watch(
	() => route.query.view,
	async () => {
		await syncView();
		load();
	}
);
onMounted(async () => {
	if (!inbox.teams.length) loadTeams();
	// the dashboard's tiles pass their team and rep along
	for (const field of ["team", "assigned_to"])
		if (route.query[field]) extra.push({ field, value: String(route.query[field]) });
	await syncView();
	load();
});
</script>
