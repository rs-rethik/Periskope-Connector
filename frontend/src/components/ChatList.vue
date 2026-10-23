<template>
	<div class="flex h-full flex-col">
		<div class="flex flex-col gap-2 border-b border-outline-gray-1 p-3">
			<div class="flex items-center justify-between">
				<h2 class="text-lg font-semibold text-ink-gray-9">{{ title }}</h2>
				<span class="text-sm text-ink-gray-5">{{ total }}</span>
			</div>
			<TextInput
				v-model="search"
				placeholder="Search name, number or message"
				:debounce="300"
			>
				<template #prefix><LucideSearch class="size-4 text-ink-gray-5" /></template>
			</TextInput>
			<div class="flex flex-wrap gap-1">
				<Button
					v-for="t in tabs"
					:key="t.key"
					size="sm"
					:variant="tab === t.key ? 'subtle' : 'ghost'"
					:label="t.label"
					@click="tab = t.key"
				/>
			</div>
		</div>

		<div ref="scroller" class="flex-1 overflow-y-auto" @scroll="onScroll">
			<button
				v-for="c in chats"
				:key="c.name"
				class="flex w-full gap-3 border-b border-outline-gray-1 px-3 py-2.5 text-left hover:bg-surface-gray-1"
				:class="c.name === selected ? 'bg-surface-gray-2 hover:bg-surface-gray-2' : ''"
				@click="$emit('select', c.name)"
			>
				<Avatar
					:label="c.chat_name || c.contact_number || c.title"
					size="lg"
					class="shrink-0"
				/>
				<div class="min-w-0 flex-1">
					<div class="flex items-center gap-2">
						<LucideUsers v-if="c.is_group" class="size-3.5 shrink-0 text-ink-gray-5" />
						<span
							class="truncate text-base"
							:class="c.unread ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-8'"
						>
							{{ c.title }}
						</span>
						<span
							class="ml-auto shrink-0 text-xs"
							:class="c.unread ? 'text-ink-green-6' : 'text-ink-gray-5'"
						>
							{{ listTime(c.last_message_at) }}
						</span>
					</div>
					<div class="mt-0.5 flex items-center gap-1.5">
						<LucideCheckCheck
							v-if="c.last_message_direction === 'Outgoing'"
							class="size-3.5 shrink-0 text-ink-gray-5"
						/>
						<span class="truncate text-sm text-ink-gray-6">{{
							c.last_message_preview || " "
						}}</span>
						<span
							v-if="c.unread"
							class="ml-auto size-2.5 shrink-0 rounded-full bg-surface-green-3"
						/>
					</div>
					<div class="mt-1 flex flex-wrap items-center gap-1">
						<Badge
							v-if="!inbox.number"
							:label="c.number_label"
							variant="outline"
							size="sm"
						/>
						<Badge
							v-if="c.ai_tag"
							:label="c.ai_tag"
							variant="subtle"
							theme="blue"
							size="sm"
						/>
						<Badge
							v-if="c.deal_title || c.crm_deal"
							:label="'Deal: ' + (c.deal_title || c.crm_deal)"
							variant="subtle"
							theme="green"
							size="sm"
						/>
						<Badge
							v-else-if="c.lead_title || c.crm_lead"
							:label="'Lead: ' + (c.lead_title || c.crm_lead)"
							variant="subtle"
							theme="orange"
							size="sm"
						/>
						<Badge
							v-if="c.status !== 'Open'"
							:label="c.status"
							variant="subtle"
							size="sm"
						/>
					</div>
				</div>
			</button>

			<div v-if="loading" class="flex justify-center p-4">
				<LoadingIndicator class="size-5" />
			</div>
			<div v-else-if="!chats.length" class="p-6 text-center text-sm text-ink-gray-5">
				No chats here.
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { Avatar, Badge, Button, LoadingIndicator, TextInput, call } from "frappe-ui";
import LucideSearch from "~icons/lucide/search";
import LucideUsers from "~icons/lucide/users";
import LucideCheckCheck from "~icons/lucide/check-check";
import { QUICK_FILTERS, currentFilters, inbox } from "@/stores/inbox";
import { numberLabel } from "@/boot";
import { listTime } from "@/utils/format";

defineProps({ selected: { type: String, default: "" } });
defineEmits(["select"]);

const tabs = [
	{ key: "all", label: "All" },
	{ key: "chats", label: "Chats" },
	{ key: "groups", label: "Groups" },
	{ key: "linked", label: "In CRM" },
	{ key: "unlinked", label: "Not in CRM" },
];
const tab = ref("all");
const search = ref("");
const chats = ref([]);
const total = ref(0);
const loading = ref(false);
const scroller = ref(null);
const PAGE = 50;

const title = computed(() => {
	const quick = QUICK_FILTERS.find((f) => f.key === inbox.quick)?.label || "Chats";
	return inbox.number ? `${quick} · ${numberLabel(inbox.number)}` : quick;
});

function filters() {
	const extra = { search: search.value };
	if (tab.value === "chats") extra.groups = 0;
	if (tab.value === "groups") extra.groups = 1;
	if (tab.value === "linked") extra.linked = 1;
	if (tab.value === "unlinked") extra.linked = 0;
	return currentFilters(extra);
}

async function load(append = false) {
	loading.value = true;
	try {
		const start = append ? chats.value.length : 0;
		// A live update reloads everything shown so far, so nobody loses their scroll position.
		const length = append ? PAGE : Math.max(PAGE, chats.value.length);
		const r = await call("periskope_connector.api.inbox.get_chats", {
			filters: filters(),
			start,
			page_length: length,
		});
		chats.value = append ? [...chats.value, ...r.chats] : r.chats;
		total.value = r.total;
	} finally {
		loading.value = false;
	}
}

function onScroll() {
	const el = scroller.value;
	if (!el || loading.value || chats.value.length >= total.value) return;
	if (el.scrollTop + el.clientHeight > el.scrollHeight - 200) load(true);
}

watch(
	() => [inbox.quick, inbox.number, tab.value, search.value],
	() => {
		chats.value = [];
		load();
	}
);
watch(
	() => inbox.version,
	() => load()
);
onMounted(() => load());

defineExpose({ chats, reload: load });
</script>
