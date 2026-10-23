<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-3">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Conversations</h1>
			<TextInput v-model="search" class="w-64" placeholder="Search" :debounce="300">
				<template #prefix><LucideSearch class="size-4 text-ink-gray-5" /></template>
			</TextInput>
			<Select v-model="status" class="w-40" :options="statusOptions" />
			<Select v-model="sortKey" class="w-48" :options="sortOptions" />
			<Button
				variant="ghost"
				:title="order === 'desc' ? 'Newest first' : 'Oldest first'"
				@click="order = order === 'desc' ? 'asc' : 'desc'"
			>
				<template #icon>
					<LucideArrowDownWideNarrow v-if="order === 'desc'" class="size-4" />
					<LucideArrowUpNarrowWide v-else class="size-4" />
				</template>
			</Button>
			<Dropdown :options="columnOptions" placement="right">
				<Button label="Columns"
					><template #prefix><LucideColumns3 class="size-4" /></template
				></Button>
			</Dropdown>
			<span class="ml-auto text-sm text-ink-gray-5">{{ total }} chats</span>
		</div>

		<div class="min-h-0 flex-1 overflow-auto px-5">
			<ListView
				:columns="shownColumns"
				:rows="rows"
				row-key="name"
				:options="{
					selectable: false,
					showTooltip: true,
					resizeColumn: true,
					onRowClick: (row) =>
						$router.push({ name: 'Conversation', params: { chat: row.name } }),
					emptyState: { title: loading ? 'Loading…' : 'No chats', description: '' },
				}"
			>
				<template #cell="{ item, row, column }">
					<div v-if="column.key === 'title'" class="flex items-center gap-2">
						<span
							v-if="row.unread"
							class="size-2 shrink-0 rounded-full bg-surface-green-3"
						/>
						<span
							class="truncate"
							:class="
								row.unread ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-8'
							"
							>{{ row.title }}</span
						>
					</div>
					<Badge
						v-else-if="column.key === 'status'"
						:label="row.status"
						variant="subtle"
						:theme="statusTheme[row.status]"
					/>
					<Badge
						v-else-if="column.key === 'ai_tag' && row.ai_tag"
						:label="row.ai_tag"
						variant="subtle"
						theme="blue"
					/>
					<span v-else-if="column.key === 'record'" class="truncate text-ink-gray-7">
						{{
							row.crm_deal
								? "Deal " + (row.deal_title || row.crm_deal)
								: row.crm_lead
								? "Lead " + (row.lead_title || row.crm_lead)
								: ""
						}}
					</span>
					<span
						v-else-if="column.key === 'assigned_to'"
						class="truncate text-ink-gray-7"
						>{{ (row.assigned_to || []).join(", ") }}</span
					>
					<span v-else-if="column.key === 'last_message_at'" class="text-ink-gray-6">{{
						fromNow(row.last_message_at)
					}}</span>
					<span v-else-if="column.key === 'contact_number'" class="text-ink-gray-7">{{
						row.contact_number ? "+" + row.contact_number : row.is_group ? "Group" : ""
					}}</span>
					<span v-else class="truncate text-ink-gray-7">{{ item }}</span>
				</template>
			</ListView>
		</div>

		<div class="flex items-center gap-2 border-t border-outline-gray-1 px-5 py-2">
			<Select
				v-model="pageLength"
				class="w-24"
				:options="[20, 50, 100].map((n) => ({ label: String(n), value: n }))"
			/>
			<span class="text-sm text-ink-gray-5">{{ rows.length }} of {{ total }}</span>
			<Button
				v-if="rows.length < total"
				class="ml-auto"
				label="Load more"
				:loading="loading"
				@click="load(true)"
			/>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { Badge, Button, Dropdown, ListView, Select, TextInput, call } from "frappe-ui";
import LucideSearch from "~icons/lucide/search";
import LucideColumns3 from "~icons/lucide/columns-3";
import LucideArrowDownWideNarrow from "~icons/lucide/arrow-down-wide-narrow";
import LucideArrowUpNarrowWide from "~icons/lucide/arrow-up-narrow-wide";
import { currentFilters, inbox } from "@/stores/inbox";
import { fromNow } from "@/utils/format";

const COLUMNS = [
	{ label: "Contact", key: "title", width: "220px" },
	{ label: "Number", key: "contact_number", width: "150px" },
	{ label: "Our number", key: "number_label", width: "150px" },
	{ label: "Last message", key: "last_message_preview", width: "320px" },
	{ label: "Status", key: "status", width: "110px" },
	{ label: "Assigned to", key: "assigned_to", width: "170px" },
	{ label: "AI tag", key: "ai_tag", width: "150px" },
	{ label: "Lead / Deal", key: "record", width: "200px" },
	{ label: "Last activity", key: "last_message_at", width: "130px" },
	{ label: "Messages", key: "message_count", width: "100px" },
];
const STORE = "periskope_inbox_columns";

function storedColumns() {
	try {
		const saved = JSON.parse(localStorage.getItem(STORE) || "null");
		if (Array.isArray(saved) && saved.length) return saved;
	} catch (e) {
		// no saved choice
	}
	return COLUMNS.filter((c) => c.key !== "message_count").map((c) => c.key);
}

const visible = ref(storedColumns());
const shownColumns = computed(() => COLUMNS.filter((c) => visible.value.includes(c.key)));
const columnOptions = computed(() =>
	COLUMNS.map((c) => ({
		label: (visible.value.includes(c.key) ? "✓ " : "   ") + c.label,
		onClick: () => {
			const keys = visible.value.includes(c.key)
				? visible.value.filter((k) => k !== c.key)
				: [...visible.value, c.key];
			visible.value = COLUMNS.map((x) => x.key).filter((k) => keys.includes(k));
			try {
				localStorage.setItem(STORE, JSON.stringify(visible.value));
			} catch (e) {
				// private window: the choice lasts for this visit
			}
		},
	}))
);

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
	{ label: "Contact name", value: "chat_name" },
	{ label: "Status", value: "status" },
	{ label: "Messages", value: "message_count" },
	{ label: "Created", value: "creation" },
];

const search = ref("");
const status = ref("active");
const sortKey = ref("last_message_at");
const order = ref("desc");
const pageLength = ref(50);
const rows = ref([]);
const total = ref(0);
const loading = ref(false);

function filters() {
	const f = currentFilters({ search: search.value });
	delete f.status;
	if (status.value !== "any") f.status = status.value;
	return f;
}

async function load(append = false) {
	loading.value = true;
	try {
		const r = await call("periskope_connector.api.inbox.get_chats", {
			filters: filters(),
			order_by: sortKey.value,
			order: order.value,
			start: append ? rows.value.length : 0,
			page_length: append ? pageLength.value : Math.max(pageLength.value, rows.value.length),
		});
		rows.value = append ? [...rows.value, ...r.chats] : r.chats;
		total.value = r.total;
	} finally {
		loading.value = false;
	}
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
	],
	() => {
		rows.value = [];
		load();
	}
);
watch(
	() => inbox.version,
	() => load()
);
onMounted(() => load());
</script>
