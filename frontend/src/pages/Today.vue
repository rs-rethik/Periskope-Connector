<template>
	<div class="today h-full overflow-y-auto">
		<div class="relative mx-auto flex max-w-6xl flex-col gap-5 px-5 py-6">
			<!-- greeting -->
			<header class="flex flex-wrap items-end justify-between gap-3">
				<div>
					<p class="text-sm text-ink-gray-5">{{ dateLabel }}</p>
					<h1
						class="mt-0.5 text-[26px] font-semibold leading-tight tracking-tight text-ink-gray-9"
					>
						{{ greeting }}, {{ data?.user.first_name || boot.full_name }}
					</h1>
				</div>
				<div class="flex items-center gap-2">
					<Tooltip :text="officeTitle"
						><div
							class="flex items-center gap-2 rounded-full border border-outline-gray-1 bg-surface-base px-3 py-1.5 text-sm shadow-sm"
						>
							<span class="relative grid size-2 place-items-center">
								<span
									v-if="office.open"
									class="absolute size-2 animate-ping rounded-full bg-surface-green-6 opacity-60"
								/>
								<span
									class="size-2 rounded-full"
									:class="
										office.open ? 'bg-surface-green-6' : 'bg-surface-gray-5'
									"
								/>
							</span>
							<span class="font-medium tabular-nums text-ink-gray-9">{{
								clock
							}}</span>
							<span class="text-ink-gray-5">{{
								office.open === undefined
									? ""
									: office.open
									? "Office open"
									: "Office closed"
							}}</span>
						</div></Tooltip
					>
					<Button
						variant="ghost"
						:loading="loading"
						tooltip="Refresh"
						aria-label="Refresh"
						@click="load"
					>
						<template #icon><LucideRefreshCw class="size-4" /></template>
					</Button>
				</div>
			</header>

			<FestivalBanner />

			<DailyBrief />

			<!-- the four numbers -->
			<section class="grid grid-cols-2 gap-3 lg:grid-cols-4">
				<button
					v-for="s in stats"
					:key="s.key"
					class="group flex min-w-0 items-start gap-3 rounded-xl border border-outline-gray-1 bg-surface-base p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
					@click="scrollTo(s.key)"
				>
					<span
						class="hidden size-9 shrink-0 place-items-center rounded-lg sm:grid"
						:class="s.chip"
					>
						<component :is="s.icon" class="size-4" />
					</span>
					<span class="min-w-0">
						<span class="block text-2xl font-semibold tabular-nums text-ink-gray-9">{{
							data ? s.value : "–"
						}}</span>
						<span class="block truncate text-sm text-ink-gray-7">{{ s.label }}</span>
						<span
							class="block truncate text-xs"
							:class="s.alert ? 'text-ink-red-6' : 'text-ink-gray-5'"
							>{{ s.hint }}</span
						>
					</span>
				</button>
			</section>

			<div class="grid items-start gap-5 lg:grid-cols-5">
				<!-- waiting for a reply -->
				<Card
					id="waiting"
					class="lg:col-span-3"
					title="Waiting for your reply"
					:subtitle="waitingSubtitle"
					:empty="!data?.waiting.rows.length"
					empty-text="Nobody is waiting on you. Nice work."
					:more="
						data?.waiting.total > data?.waiting.rows.length
							? `All ${data.waiting.total}`
							: ''
					"
					@more="openUnanswered"
				>
					<button
						v-for="c in data?.waiting.rows"
						:key="c.name"
						class="row"
						@click="openChat(c.name)"
					>
						<InitialsAvatar size="sm" :name="c.title" />
						<span class="min-w-0 flex-1">
							<span class="flex items-center gap-2">
								<span class="truncate font-medium text-ink-gray-9">{{
									c.title
								}}</span>
								<span
									v-if="c.lead_title || c.deal_title"
									class="pill bg-surface-blue-1 text-ink-blue-7"
								>
									{{ c.deal_title ? "Deal" : "Lead" }}
								</span>
							</span>
							<span class="block truncate text-sm text-ink-gray-5">{{
								c.last_message_preview || "…"
							}}</span>
						</span>
						<span class="flex shrink-0 flex-col items-end gap-1">
							<span class="text-xs font-medium tabular-nums" :class="waitClass(c)">{{
								waited(c.waiting_since)
							}}</span>
							<span class="flex items-center gap-1">
								<SlaBadge :chat="c" />
								<span class="pill bg-surface-gray-2 text-ink-gray-6">{{
									c.number_label
								}}</span>
							</span>
						</span>
					</button>
				</Card>

				<!-- tasks -->
				<Card
					id="tasks"
					class="lg:col-span-2"
					title="Tasks due"
					:subtitle="
						data?.tasks.overdue
							? `${data.tasks.overdue} overdue`
							: 'Due today or earlier'
					"
					:empty="!data?.tasks.rows.length"
					empty-text="No tasks due. You're all caught up."
					:more="
						data?.tasks.total > data?.tasks.rows.length
							? `All ${data.tasks.total}`
							: ''
					"
					@more="goTo('/crm/tasks')"
				>
					<div v-for="t in data?.tasks.rows" :key="t.name" class="row">
						<Tooltip :text="'Mark done'"
							><button
								class="grid size-5 shrink-0 place-items-center rounded-full border border-outline-gray-3 text-transparent transition hover:border-outline-green-3 hover:bg-surface-green-2 hover:text-ink-green-7"
								:aria-label="'Mark done'"
								@click="done(t)"
							>
								<LucideCheck class="size-3" /></button
						></Tooltip>
						<button
							class="min-w-0 flex-1 text-left"
							@click="openRecord(t.reference_doctype, t.reference_docname)"
						>
							<span class="block truncate font-medium text-ink-gray-9">{{
								t.title
							}}</span>
							<span class="block truncate text-sm text-ink-gray-5">{{
								t.reference_title || "No lead or deal"
							}}</span>
						</button>
						<span
							class="shrink-0 text-xs font-medium"
							:class="t.overdue ? 'text-ink-red-6' : 'text-ink-gray-6'"
							>{{ dueLabel(t) }}</span
						>
					</div>
				</Card>

				<!-- going quiet -->
				<Card
					id="quiet"
					class="lg:col-span-3"
					title="Going quiet"
					:subtitle="`Your open leads and deals with no activity for ${
						data?.quiet.days || 7
					}+ days`"
					:empty="!data?.quiet.rows.length"
					empty-text="Every lead and deal of yours moved this week."
					:more="
						data?.quiet.total > data?.quiet.rows.length
							? `All ${data.quiet.total}`
							: ''
					"
					@more="goTo('/crm/leads')"
				>
					<button
						v-for="q in data?.quiet.rows"
						:key="q.doctype + q.name"
						class="row"
						@click="goTo(q.route)"
					>
						<span
							class="grid size-8 shrink-0 place-items-center rounded-lg"
							:class="
								q.doctype === 'CRM Deal'
									? 'bg-surface-violet-1 text-ink-violet-7'
									: 'bg-surface-amber-1 text-ink-amber-7'
							"
						>
							<component
								:is="q.doctype === 'CRM Deal' ? LucideHandshake : LucideUserRound"
								class="size-4"
							/>
						</span>
						<span class="min-w-0 flex-1">
							<span class="block truncate font-medium text-ink-gray-9">{{
								q.title || q.name
							}}</span>
							<span class="block truncate text-sm text-ink-gray-5"
								>{{ q.name }} · {{ q.status }}</span
							>
						</span>
						<span class="shrink-0 text-right">
							<span class="block text-sm font-semibold tabular-nums text-ink-gray-8"
								>{{ q.idle_days }} days</span
							>
							<span class="block text-xs text-ink-gray-5">quiet</span>
						</span>
					</button>
				</Card>

				<!-- new today -->
				<Card
					id="new"
					class="lg:col-span-2"
					title="New today"
					subtitle="Numbers that wrote for the first time"
					:empty="!data?.new.rows.length"
					empty-text="No new numbers yet today."
				>
					<button
						v-for="n in data?.new.rows"
						:key="n.name"
						class="row"
						@click="openChat(n.name)"
					>
						<InitialsAvatar size="sm" :name="n.title" />
						<span class="min-w-0 flex-1">
							<span class="block truncate font-medium text-ink-gray-9">{{
								n.title
							}}</span>
							<span class="block truncate text-sm text-ink-gray-5">{{
								n.last_message_preview || "…"
							}}</span>
						</span>
						<span class="flex shrink-0 flex-col items-end gap-1">
							<span class="text-xs tabular-nums text-ink-gray-5">{{
								messageTime(n.creation)
							}}</span>
							<span
								v-if="n.welcomed"
								class="pill bg-surface-green-2 text-ink-green-8"
							>
								<LucideSparkles class="size-3" />AI welcomed
							</span>
						</span>
					</button>
				</Card>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import dayjs from "dayjs";
import { Button, Tooltip, call } from "frappe-ui";
import LucideRefreshCw from "~icons/lucide/refresh-cw";
import LucideMessageCircleQuestion from "~icons/lucide/message-circle-question";
import LucideListChecks from "~icons/lucide/list-checks";
import LucideMoon from "~icons/lucide/moon";
import LucideSparkles from "~icons/lucide/sparkles";
import LucideCheck from "~icons/lucide/check";
import LucideHandshake from "~icons/lucide/handshake";
import LucideUserRound from "~icons/lucide/user-round";
import SlaBadge from "@/components/SlaBadge.vue";
import InitialsAvatar from "@/components/InitialsAvatar.vue";
import FestivalBanner from "@/components/FestivalBanner.vue";
import DailyBrief from "@/components/DailyBrief.vue";
import { setFestival } from "@/utils/festival";
import { boot } from "@/boot";
import { inbox } from "@/stores/inbox";
import { messageTime } from "@/utils/format";
import { showError } from "@/utils/errors";

const Card = defineComponent({
	inheritAttrs: false,
	props: { title: String, subtitle: String, empty: Boolean, emptyText: String, more: String },
	emits: ["more"],
	setup(props, { slots, emit, attrs }) {
		return () =>
			h(
				"section",
				{
					...attrs,
					class: [
						"flex min-w-0 scroll-mt-4 flex-col rounded-xl border border-outline-gray-1 bg-surface-base shadow-sm",
						attrs.class,
					],
				},
				[
					h("div", { class: "flex items-start justify-between gap-3 px-4 pb-2 pt-4" }, [
						h("div", { class: "min-w-0" }, [
							h(
								"h2",
								{ class: "text-base font-semibold text-ink-gray-9" },
								props.title
							),
							props.subtitle
								? h(
										"p",
										{ class: "truncate text-sm text-ink-gray-5" },
										props.subtitle
								  )
								: null,
						]),
						props.more
							? h(
									"button",
									{
										class: "shrink-0 rounded px-2 py-1 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2",
										onClick: () => emit("more"),
									},
									`${props.more} →`
							  )
							: null,
					]),
					props.empty
						? h(
								"p",
								{ class: "px-4 pb-6 pt-3 text-sm text-ink-gray-5" },
								props.emptyText
						  )
						: h("div", { class: "flex flex-col px-2 pb-2" }, slots.default?.()),
				]
			);
	},
});

const router = useRouter();
const data = ref(null);
const loading = ref(false);
const now = ref(dayjs());
let tick, poll;

async function load() {
	loading.value = true;
	try {
		data.value = await call("periskope_connector.api.today.get_today");
		setFestival(data.value.festival);
	} catch (e) {
		showError(e, "Could not load Today.");
	} finally {
		loading.value = false;
	}
}

onMounted(() => {
	load();
	tick = setInterval(() => (now.value = dayjs()), 15000);
	poll = setInterval(load, 120000);
});
onUnmounted(() => {
	clearInterval(tick);
	clearInterval(poll);
});
// a new message or a reply changes who is waiting
let pending;
watch(
	() => inbox.version,
	() => {
		clearTimeout(pending);
		pending = setTimeout(load, 1500);
	}
);

const office = computed(() => data.value?.office || {});
const clock = computed(() => now.value.format("h:mm A"));
const dateLabel = computed(() => now.value.format("dddd, D MMMM"));
const greeting = computed(() => {
	const hour = now.value.hour();
	return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
});
const officeTitle = computed(() =>
	office.value.start ? `Working hours ${office.value.start}–${office.value.end}` : ""
);

const stats = computed(() => {
	const d = data.value;
	return [
		{
			key: "waiting",
			label: "Waiting on you",
			value: d?.waiting.total ?? 0,
			hint: d?.waiting.breached
				? `${d.waiting.breached} past SLA`
				: "Customers who wrote last",
			alert: !!d?.waiting.breached,
			icon: LucideMessageCircleQuestion,
			chip: "bg-surface-green-2 text-ink-green-8",
		},
		{
			key: "tasks",
			label: "Tasks due",
			value: d?.tasks.total ?? 0,
			hint: d?.tasks.overdue ? `${d.tasks.overdue} overdue` : "Today or earlier",
			alert: !!d?.tasks.overdue,
			icon: LucideListChecks,
			chip: "bg-surface-blue-2 text-ink-blue-7",
		},
		{
			key: "quiet",
			label: "Going quiet",
			value: d?.quiet.total ?? 0,
			hint: `No activity ${d?.quiet.days || 7}+ days`,
			icon: LucideMoon,
			chip: "bg-surface-amber-2 text-ink-amber-8",
		},
		{
			key: "new",
			label: "New today",
			value: d?.new.total ?? 0,
			hint: "First message today",
			icon: LucideSparkles,
			chip: "bg-surface-violet-2 text-ink-violet-7",
		},
	];
});

const waitingSubtitle = computed(() => {
	const d = data.value?.waiting;
	if (!d) return "";
	return d.breached
		? `Longest first · ${d.breached} past SLA`
		: "Your chats and unassigned ones, longest first";
});

function waited(since) {
	if (!since) return "";
	const mins = Math.max(0, now.value.diff(dayjs(since), "minute"));
	if (mins < 60) return `${mins}m`;
	if (mins < 60 * 24) return `${Math.floor(mins / 60)}h ${mins % 60}m`;
	return `${Math.floor(mins / 1440)}d`;
}

function waitClass(c) {
	if (c.sla === "Breached") return "text-ink-red-6";
	const mins = now.value.diff(dayjs(c.waiting_since), "minute");
	return mins > 60 ? "text-ink-amber-7" : "text-ink-gray-6";
}

function dueLabel(t) {
	if (!t.due_date) return "";
	const due = dayjs(t.due_date);
	if (t.overdue) {
		const days = now.value.startOf("day").diff(due.startOf("day"), "day");
		return days === 1 ? "Yesterday" : `${days} days late`;
	}
	return due.format("h:mm A") === "12:00 AM" ? "Today" : `Today ${due.format("h:mm A")}`;
}

function scrollTo(id) {
	document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function openChat(name) {
	router.push({ name: "Inbox", params: { chat: name } });
}

function openUnanswered() {
	inbox.quick = "unanswered";
	router.push({ name: "Inbox" });
}

function openRecord(doctype, name) {
	if (!name) return;
	goTo(`/crm/${doctype === "CRM Deal" ? "deals" : "leads"}/${name}`);
}

function goTo(url) {
	window.location.href = url;
}

async function done(task) {
	try {
		await call("periskope_connector.api.today.complete_task", { task: task.name });
		await load();
	} catch (e) {
		showError(e, "Could not mark the task done.");
	}
}
</script>

<style scoped>
.today :deep(.row) {
	display: flex;
	width: 100%;
	align-items: center;
	gap: 0.75rem;
	border-radius: 0.5rem;
	padding: 0.5rem 0.5rem;
	text-align: left;
	transition: background-color 0.15s;
}
.today :deep(.row:hover) {
	background: rgb(128 128 128 / 0.08);
}
.today :deep(.avatar) {
	display: grid;
	place-items: center;
	flex-shrink: 0;
	width: 2rem;
	height: 2rem;
	border-radius: 9999px;
	font-size: 0.75rem;
	font-weight: 600;
}
.today :deep(.pill) {
	display: inline-flex;
	align-items: center;
	gap: 0.25rem;
	border-radius: 9999px;
	padding: 0.05rem 0.45rem;
	font-size: 0.7rem;
	font-weight: 500;
	white-space: nowrap;
}
</style>
