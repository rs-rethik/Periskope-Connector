<template>
	<div class="flex h-full flex-col">
		<div class="flex flex-col gap-2.5 border-b border-outline-gray-1 p-3 pb-2.5">
			<div class="flex items-center gap-2">
				<h2 class="truncate text-lg font-semibold text-ink-gray-9">{{ title }}</h2>
				<span class="text-sm text-ink-gray-5">{{ total }}</span>
				<Button
					v-if="boot.numbers.length"
					class="tap ml-auto"
					size="sm"
					label="New chat"
					@click="newChat = true"
				>
					<template #prefix><LucideSquarePen class="size-4" /></template>
				</Button>
				<Dropdown
					v-if="boot.is_manager && boot.numbers.some((n) => n.source !== 'Meta')"
					:options="[
						{ label: 'New group', onClick: () => (newGroup = true) },
						{ label: 'Join with an invite link', onClick: () => (joinGroup = true) },
					]"
				>
					<Button
						class="tap"
						size="sm"
						tooltip="WhatsApp groups"
						aria-label="WhatsApp groups"
					>
						<template #icon><LucideUsersRound class="size-4" /></template>
					</Button>
				</Dropdown>
			</div>
			<NewChatDialog v-model="newChat" />
			<NewGroupDialog v-if="boot.is_manager" v-model="newGroup" />
			<JoinGroupDialog v-if="boot.is_manager" v-model="joinGroup" />
			<div v-if="isMobile && boot.numbers.length > 1" class="flex">
				<NumberSwitcher compact />
			</div>
			<TextInput
				v-model="search"
				class="search-pill"
				placeholder="Search name, number or message"
				:debounce="300"
			>
				<template #prefix><LucideSearch class="size-4 text-ink-gray-5" /></template>
			</TextInput>
			<div
				class="fade-right flex gap-0.5 overflow-x-auto rounded-full bg-surface-gray-2 p-1 [scrollbar-width:none]"
			>
				<button
					v-for="t in tabs"
					:key="t.key"
					class="flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-sm transition max-md:min-h-10"
					:class="
						tab === t.key
							? 'bg-surface-base font-medium text-ink-gray-9 shadow-sm'
							: 'text-ink-gray-6 hover:text-ink-gray-8'
					"
					@click="tab = t.key"
				>
					{{ t.label }}
					<span
						v-if="tab === t.key && total"
						class="rounded-full bg-surface-gray-3 px-1.5 text-xs tabular-nums text-ink-gray-7"
						>{{ total }}</span
					>
				</button>
			</div>
		</div>

		<div
			ref="scroller"
			class="flex-1 overflow-y-auto px-2 py-2 transition-opacity"
			:class="switching ? 'opacity-60' : ''"
			@scroll="onScroll"
		>
			<button
				v-for="c in chats"
				:key="c.name"
				class="relative mb-0.5 flex w-full gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-surface-gray-1"
				:class="c.name === selected ? 'bg-surface-green-1 hover:bg-surface-green-1' : ''"
				@click="select(c)"
			>
				<span
					v-if="c.name === selected"
					class="absolute inset-y-2 left-0 w-[3px] rounded-full bg-surface-green-6"
				/>
				<InitialsAvatar :name="c.chat_name || c.contact_number || c.title" />
				<div class="min-w-0 flex-1">
					<div class="flex items-center gap-2">
						<LucideUsers v-if="c.is_group" class="size-3.5 shrink-0 text-ink-gray-5" />
						<span
							class="truncate text-base"
							:class="c.unread ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-8'"
						>
							{{ c.title }}
						</span>
						<Tooltip v-if="waiting(c)" text="Waiting on us"
							><span class="size-1.5 shrink-0 rounded-full bg-surface-amber-6"
						/></Tooltip>
						<span
							class="ml-auto shrink-0 text-xs"
							:class="c.unread ? 'font-medium text-ink-green-7' : 'text-ink-gray-5'"
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
							class="ml-auto size-2.5 shrink-0 rounded-full bg-surface-green-6 ring-2 ring-surface-green-2"
						/>
					</div>
					<div class="mt-1 flex flex-wrap items-center gap-1">
						<Badge
							v-if="!inbox.number"
							:label="
								c.source === 'Meta' ? `${c.number_label} · Meta` : c.number_label
							"
							variant="outline"
							size="sm"
						/>
						<Badge
							v-if="c.is_group && c.organization"
							:label="c.organization"
							variant="subtle"
							theme="gray"
							size="sm"
						>
							<template #prefix><LucideBuilding2 class="size-3" /></template>
						</Badge>
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
							v-if="c.link_review"
							label="Check CRM link"
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
						<Badge
							v-for="l in c.labels"
							:key="l"
							:label="l"
							variant="outline"
							size="sm"
						/>
						<SlaBadge v-if="c.sla && c.sla !== 'On Track'" :chat="c" />
						<InitialsAvatar
							v-if="c.assigned_to.length"
							class="ml-auto"
							size="xs"
							:name="nameFromEmail(c.assigned_to[0])"
							:tip="'Assigned to ' + c.assigned_to.join(', ')"
						/>
					</div>
				</div>
			</button>

			<!-- what was written: the messages that have the searched words -->
			<div v-if="found.length" class="mt-2 border-t border-outline-gray-1 pt-2">
				<div class="px-3 pb-1 text-xs font-medium uppercase text-ink-gray-5">
					In messages
				</div>
				<button
					v-for="m in found"
					:key="m.name"
					class="mb-0.5 flex w-full flex-col rounded-xl px-3 py-2 text-left hover:bg-surface-gray-1"
					@click="openFound(m)"
				>
					<span class="flex items-center gap-2">
						<span class="truncate text-sm font-medium text-ink-gray-8">{{
							m.title
						}}</span>
						<span class="ml-auto shrink-0 text-xs text-ink-gray-5">{{
							listTime(m.message_timestamp)
						}}</span>
					</span>
					<span class="line-clamp-2 text-sm text-ink-gray-6">{{ m.snippet }}</span>
				</button>
			</div>

			<div v-if="loading && (!chats.length || appending)" class="flex justify-center p-4">
				<LoadingIndicator class="size-5" />
			</div>
			<div
				v-else-if="failed && !chats.length"
				class="flex flex-col items-center gap-2 p-6 text-center text-sm text-ink-gray-5"
			>
				Could not load the chats.
				<Button size="sm" label="Retry" @click="load()" />
			</div>
			<div
				v-else-if="!chats.length && !loading"
				class="p-6 text-center text-sm text-ink-gray-5"
			>
				No chats here.
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Badge, Button, Dropdown, LoadingIndicator, TextInput, Tooltip, call } from "frappe-ui";
import InitialsAvatar from "@/components/InitialsAvatar.vue";
import LucideSearch from "~icons/lucide/search";
import LucideUsers from "~icons/lucide/users";
import LucideBuilding2 from "~icons/lucide/building-2";
import LucideCheckCheck from "~icons/lucide/check-check";
import { QUICK_FILTERS, currentFilters, inbox } from "@/stores/inbox";
import LucideSquarePen from "~icons/lucide/square-pen";
import LucideUsersRound from "~icons/lucide/users-round";
import SlaBadge from "@/components/SlaBadge.vue";
import NumberSwitcher from "@/components/NumberSwitcher.vue";
import { isMobile } from "@/utils/screen";
import { boot, numberLabel } from "@/boot";
import { listTime, nameFromEmail } from "@/utils/format";
import { loadFailed } from "@/utils/load";

// Opened now and then: loaded apart from the list itself.
const NewChatDialog = defineAsyncComponent(() => import("@/components/NewChatDialog.vue"));
const NewGroupDialog = defineAsyncComponent(() => import("@/components/NewGroupDialog.vue"));
const JoinGroupDialog = defineAsyncComponent(() => import("@/components/JoinGroupDialog.vue"));

const props = defineProps({ selected: { type: String, default: "" } });
const router = useRouter();
const emit = defineEmits(["select"]);

// Opening a chat reads it: show that at once rather than after the next live update.
function select(c) {
	if (c.unread)
		chats.value = chats.value.map((x) => (x.name === c.name ? { ...x, unread: 0 } : x));
	emit("select", c.name);
}

const newChat = ref(false);
const newGroup = ref(false); // NewGroupDialog (managers)
const joinGroup = ref(false); // JoinGroupDialog (managers)
// the customer wrote last and the chat is still open: our move
const waiting = (c) =>
	c.last_message_direction === "Incoming" && c.status === "Open" && !c.is_group;
const tabs = [
	{ key: "all", label: "All" },
	{ key: "chats", label: "Chats" },
	{ key: "groups", label: "Groups" },
	{ key: "internal", label: "Internal groups" },
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
const appending = ref(false);
const failed = ref(false); // the first load failed (not "no chats")
const switching = ref(false); // a filter changed: dim the old rows until the new ones come
let latest = 0; // only the newest request may fill the list (fast typing, quick filter changes)

const title = computed(() => {
	const quick = QUICK_FILTERS.find((f) => f.key === inbox.quick)?.label || "Chats";
	return inbox.number ? `${quick} · ${numberLabel(inbox.number)}` : quick;
});

function filters() {
	const extra = { search: search.value };
	if (tab.value === "chats") extra.groups = 0;
	if (tab.value === "groups") extra.groups = 1;
	// internal (team) groups only in their own tab
	extra.internal = tab.value === "internal" ? 1 : 0;
	if (tab.value === "internal") delete extra.groups;
	if (tab.value === "linked") extra.linked = 1;
	if (tab.value === "unlinked") extra.linked = 0;
	return currentFilters(extra);
}

async function load(append = false) {
	const ticket = ++latest;
	loading.value = true;
	appending.value = append;
	try {
		const start = append ? chats.value.length : 0;
		// A live update reloads everything shown so far, so nobody loses their scroll position.
		const length = append || switching.value ? PAGE : Math.max(PAGE, chats.value.length);
		const r = await call("periskope_connector.api.inbox.get_chats", {
			filters: filters(),
			start,
			page_length: length,
		});
		if (ticket !== latest) return;
		chats.value = append ? [...chats.value, ...r.chats] : r.chats;
		total.value = r.total;
		failed.value = false;
	} catch (e) {
		if (ticket !== latest) return;
		failed.value = !chats.value.length; // rows already shown stay; an empty list says why
		loadFailed(e, "Could not load the chats.");
	} finally {
		if (ticket === latest) loading.value = appending.value = switching.value = false;
	}
}

// A phone hides the list while a chat is open (pages/Inbox.vue), which forgets how far it was
// scrolled: Back puts it where it was.
let scrolled = 0;
watch(
	() => props.selected,
	(now) => now || nextTick(() => scroller.value?.scrollTo({ top: scrolled }))
);

function onScroll() {
	const el = scroller.value;
	if (el?.clientHeight) scrolled = el.scrollTop;
	if (!el || loading.value || chats.value.length >= total.value) return;
	if (el.scrollTop + el.clientHeight > el.scrollHeight - 200) load(true);
}

watch(
	() => [inbox.quick, inbox.number, tab.value, search.value],
	() => {
		// the current rows stay (dimmed) until the new ones arrive, so the list does not flash empty
		scroller.value?.scrollTo({ top: 0 });
		switching.value = true;
		load();
	}
);
watch(
	() => inbox.version,
	() => load()
);

// The messages that have the searched words (3 letters or more), under the chats.
const found = ref([]);
let findTicket = 0;
watch(search, async (q) => {
	const ticket = ++findTicket;
	if ((q || "").trim().length < 3) {
		found.value = [];
		return;
	}
	try {
		const rows = await call("periskope_connector.api.inbox.search_messages", { query: q });
		if (ticket === findTicket) found.value = rows || [];
	} catch (e) {
		if (ticket === findTicket) found.value = [];
	}
});
function openFound(m) {
	router.push({ name: "Inbox", params: { chat: m.chat }, query: { msg: m.name } });
}
onMounted(() => load());

defineExpose({ chats, reload: load });
</script>
