<template>
	<div class="dashboard flex h-full min-h-0 flex-col">
		<!-- Filters, in one row above everything -->
		<div class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-3">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Dashboard</h1>
			<div class="flex rounded bg-surface-gray-2 p-0.5">
				<button
					v-for="p in presets"
					:key="p.days"
					class="rounded px-2.5 py-1 text-sm"
					:class="
						preset === p.days
							? 'bg-surface-base text-ink-gray-9 shadow-sm'
							: 'text-ink-gray-6'
					"
					@click="setPreset(p.days)"
				>
					{{ p.label }}
				</button>
			</div>
			<div class="w-36">
				<FormControl v-model="from" type="date" @update:model-value="preset = 0" />
			</div>
			<span class="text-ink-gray-5">–</span>
			<div class="w-36">
				<FormControl v-model="to" type="date" @update:model-value="preset = 0" />
			</div>
			<Select
				v-model="number"
				class="w-40"
				:options="[
					{ label: 'All numbers', value: '' },
					...boot.numbers.map((n) => ({ label: n.label, value: n.name })),
				]"
			/>
			<Select
				v-model="team"
				class="w-40"
				:options="[
					{ label: 'All teams', value: '' },
					...inbox.teams.map((t) => ({ label: t.name, value: t.name })),
				]"
			/>
			<Select v-model="rep" class="w-44" :options="repOptions" />
			<LoadingIndicator v-if="loading" class="ml-auto size-4" />
		</div>

		<div v-if="d" class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
			<!-- Work in hand and speed -->
			<div class="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
				<StatTile
					label="Open chats"
					:value="d.tiles.open"
					:icon="LucideInbox"
					to
					@open="go({ quick: 'all' })"
				/>
				<StatTile
					label="Unassigned"
					:value="d.tiles.unassigned"
					:icon="LucideUserX"
					to
					@open="go({ quick: 'unassigned' })"
				/>
				<StatTile
					:label="`Waiting over ${d.tiles.waiting_minutes} min`"
					:value="d.tiles.waiting_long"
					:hint="`${d.tiles.waiting} waiting in all`"
					:icon="LucideHourglass"
					:tone="d.tiles.waiting_long ? 'warning' : ''"
					to
					@open="go({ quick: 'unanswered' })"
				/>
				<StatTile
					label="SLA breached now"
					:value="d.tiles.breached"
					:icon="LucideTimerOff"
					:tone="d.tiles.breached ? 'critical' : 'good'"
					to
					@open="go({ quick: 'breached' })"
				/>
				<StatTile
					label="First reply (median)"
					:value="duration(d.response.median_seconds)"
					:hint="`average ${duration(d.response.avg_seconds) || '—'} · ${
						d.response.answered
					} of ${d.response.waits} answered`"
					:icon="LucideTimer"
				/>
				<StatTile
					label="Replied within SLA"
					:value="d.response.sla_percent == null ? null : `${d.response.sla_percent}%`"
					:hint="
						d.response.sla_judged
							? `of ${d.response.sla_judged} waits with an SLA`
							: 'no SLA policy covers these chats'
					"
					:icon="LucideBadgeCheck"
					:tone="
						d.response.sla_percent == null
							? ''
							: d.response.sla_percent >= 90
							? 'good'
							: 'warning'
					"
				/>
				<StatTile label="Pending" :value="d.tiles.pending" :icon="LucideClock" />
				<StatTile
					label="Resolved"
					:value="d.tiles.resolved"
					hint="in this period"
					:icon="LucideCheckCheck"
				/>
				<StatTile
					label="Messages received"
					:value="d.tiles.received"
					:icon="LucideArrowDownLeft"
				/>
				<StatTile label="Messages sent" :value="d.tiles.sent" :icon="LucideArrowUpRight" />
				<StatTile
					label="Leads from WhatsApp"
					:value="d.leads.created"
					:icon="LucideUserPlus"
				/>
				<StatTile
					label="Lead → deal"
					:value="d.leads.rate == null ? null : `${d.leads.rate}%`"
					:hint="`${d.leads.converted} converted`"
					:icon="LucideHandshake"
				/>
			</div>

			<!-- Charts -->
			<div class="mt-5 grid gap-4 xl:grid-cols-[2fr,1fr]">
				<section class="rounded-lg border border-outline-gray-1 bg-surface-base p-4">
					<h2 class="mb-2 text-base font-medium text-ink-gray-9">Messages per day</h2>
					<DayBars :days="d.per_day" title="Messages received and sent per day" />
				</section>
				<section class="rounded-lg border border-outline-gray-1 bg-surface-base p-4">
					<h2 class="mb-2 text-base font-medium text-ink-gray-9">Chats by AI tag</h2>
					<TagBars :rows="d.tags" />
				</section>
			</div>

			<!-- Reps, numbers, teams -->
			<div class="mt-5 grid gap-4 xl:grid-cols-2">
				<section class="rounded-lg border border-outline-gray-1 bg-surface-base p-4">
					<h2 class="mb-2 text-base font-medium text-ink-gray-9">Reps</h2>
					<table class="w-full text-sm">
						<thead class="text-left text-xs text-ink-gray-5">
							<tr>
								<th class="py-1.5">Rep</th>
								<th class="py-1.5 text-right">Open now</th>
								<th class="py-1.5 text-right">Answered</th>
								<th class="py-1.5 text-right">Avg first reply</th>
								<th class="py-1.5 text-right">Within SLA</th>
								<th class="py-1.5 text-right">Resolved</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="r in d.leaderboard"
								:key="r.name"
								class="border-t border-outline-gray-1"
							>
								<td class="py-1.5 text-ink-gray-8">{{ r.name }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ r.open }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ r.answered }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">
									{{ duration(r.avg_seconds) || "—" }}
								</td>
								<td class="py-1.5 text-right text-ink-gray-8">
									{{ r.sla_percent == null ? "—" : `${r.sla_percent}%` }}
								</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ r.resolved }}</td>
							</tr>
							<tr v-if="!d.leaderboard.length">
								<td colspan="6" class="py-4 text-center text-ink-gray-5">
									No replies in this period.
								</td>
							</tr>
						</tbody>
					</table>
				</section>

				<section class="rounded-lg border border-outline-gray-1 bg-surface-base p-4">
					<h2 class="mb-2 text-base font-medium text-ink-gray-9">WhatsApp numbers</h2>
					<table class="w-full text-sm">
						<thead class="text-left text-xs text-ink-gray-5">
							<tr>
								<th class="py-1.5">Number</th>
								<th class="py-1.5">Connection</th>
								<th class="py-1.5 text-right">In / out</th>
								<th class="py-1.5 text-right">Avg first reply</th>
								<th class="py-1.5 text-right">Waiting</th>
								<th class="py-1.5 text-right">Leads</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="n in d.by_number"
								:key="n.number"
								class="border-t border-outline-gray-1"
							>
								<td class="py-1.5 text-ink-gray-8">{{ n.label }}</td>
								<td class="py-1.5">
									<span class="inline-flex items-center gap-1.5 text-ink-gray-7">
										<LucideCircleCheck
											v-if="n.is_ready"
											class="size-3.5 text-ink-green-6"
										/>
										<LucideCircleAlert
											v-else
											class="size-3.5 text-ink-red-4"
										/>
										{{
											n.is_ready
												? "Connected"
												: n.wa_state || "Not connected"
										}}
									</span>
								</td>
								<td class="py-1.5 text-right text-ink-gray-8">
									{{ n.received }} / {{ n.sent }}
								</td>
								<td class="py-1.5 text-right text-ink-gray-8">
									{{ duration(n.avg_seconds) || "—" }}
								</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ n.waiting }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ n.leads }}</td>
							</tr>
						</tbody>
					</table>
				</section>

				<section
					v-if="d.by_team.length"
					class="rounded-lg border border-outline-gray-1 bg-surface-base p-4"
				>
					<h2 class="mb-2 text-base font-medium text-ink-gray-9">Teams</h2>
					<table class="w-full text-sm">
						<thead class="text-left text-xs text-ink-gray-5">
							<tr>
								<th class="py-1.5">Team</th>
								<th class="py-1.5 text-right">Waits</th>
								<th class="py-1.5 text-right">Answered</th>
								<th class="py-1.5 text-right">Median first reply</th>
								<th class="py-1.5 text-right">Within SLA</th>
							</tr>
						</thead>
						<tbody>
							<tr
								v-for="t in d.by_team"
								:key="t.key"
								class="border-t border-outline-gray-1"
							>
								<td class="py-1.5 text-ink-gray-8">{{ t.label }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ t.waits }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">{{ t.answered }}</td>
								<td class="py-1.5 text-right text-ink-gray-8">
									{{ duration(t.median_seconds) || "—" }}
								</td>
								<td class="py-1.5 text-right text-ink-gray-8">
									{{ t.sla_percent == null ? "—" : `${t.sla_percent}%` }}
								</td>
							</tr>
						</tbody>
					</table>
				</section>

				<section class="rounded-lg border border-outline-gray-1 bg-surface-base p-4">
					<h2 class="mb-2 flex items-center gap-2 text-base font-medium text-ink-gray-9">
						<LucideTimerOff class="size-4 text-ink-red-4" /> SLA breached now
					</h2>
					<div
						v-if="!d.breaches.length"
						class="py-4 text-center text-sm text-ink-gray-5"
					>
						Nobody is waiting past the SLA.
					</div>
					<button
						v-for="b in d.breaches"
						:key="b.name"
						class="flex w-full items-center gap-3 border-t border-outline-gray-1 py-2 text-left text-sm first:border-0 hover:bg-surface-gray-1"
						@click="$router.push({ name: 'Inbox', params: { chat: b.name } })"
					>
						<span class="min-w-0 flex-1 truncate text-ink-gray-8">{{ b.title }}</span>
						<span class="text-xs text-ink-gray-5">{{ b.number_label }}</span>
						<span class="text-xs text-ink-gray-5">{{
							b.assigned_to.length ? b.assigned_to[0].split("@")[0] : "Unassigned"
						}}</span>
						<span class="text-xs font-medium text-ink-red-4">{{ late(b.due) }}</span>
					</button>
				</section>
			</div>
		</div>
		<div v-else-if="error" class="p-10 text-center text-sm text-ink-gray-6">{{ error }}</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import dayjs from "dayjs";
import { FormControl, LoadingIndicator, Select, call, debounce } from "frappe-ui";
import LucideInbox from "~icons/lucide/inbox";
import LucideUserX from "~icons/lucide/user-x";
import LucideHourglass from "~icons/lucide/hourglass";
import LucideTimerOff from "~icons/lucide/timer-off";
import LucideTimer from "~icons/lucide/timer";
import LucideBadgeCheck from "~icons/lucide/badge-check";
import LucideClock from "~icons/lucide/clock";
import LucideCheckCheck from "~icons/lucide/check-check";
import LucideArrowDownLeft from "~icons/lucide/arrow-down-left";
import LucideArrowUpRight from "~icons/lucide/arrow-up-right";
import LucideUserPlus from "~icons/lucide/user-plus";
import LucideHandshake from "~icons/lucide/handshake";
import LucideCircleCheck from "~icons/lucide/circle-check";
import LucideCircleAlert from "~icons/lucide/circle-alert";
import StatTile from "@/components/dashboard/StatTile.vue";
import DayBars from "@/components/dashboard/DayBars.vue";
import TagBars from "@/components/dashboard/TagBars.vue";
import { boot } from "@/boot";
import { inbox, loadTeams } from "@/stores/inbox";

const router = useRouter();
const presets = [
	{ label: "Today", days: 1 },
	{ label: "7 days", days: 7 },
	{ label: "30 days", days: 30 },
	{ label: "90 days", days: 90 },
];
const preset = ref(7);
const to = ref(dayjs().format("YYYY-MM-DD"));
const from = ref(dayjs().subtract(6, "day").format("YYYY-MM-DD"));
const number = ref("");
const team = ref("");
const rep = ref("");
const d = ref(null);
const loading = ref(false);
const error = ref("");
const reps = ref([]);

const repOptions = computed(() => [
	{ label: "All reps", value: "" },
	...reps.value.map((r) => ({ label: r.label || r.value, value: r.value })),
]);

function setPreset(days) {
	preset.value = days;
	to.value = dayjs().format("YYYY-MM-DD");
	from.value = dayjs()
		.subtract(days - 1, "day")
		.format("YYYY-MM-DD");
}

function duration(seconds) {
	if (seconds == null) return null;
	if (seconds < 60) return `${Math.round(seconds)}s`;
	if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
	if (seconds < 86400) return `${(seconds / 3600).toFixed(seconds < 36000 ? 1 : 0)}h`;
	return `${(seconds / 86400).toFixed(1)}d`;
}

function late(due) {
	if (!due) return "late";
	const mins = Math.round((dayjs() - dayjs(due)) / 60000);
	return mins >= 60 ? `${Math.round(mins / 60)}h late` : `${mins}m late`;
}

function go({ quick }) {
	inbox.quick = quick;
	inbox.number = number.value;
	router.push({ name: "Conversations" });
}

const load = debounce(async () => {
	loading.value = true;
	error.value = "";
	try {
		d.value = await call("periskope_connector.api.dashboard.get_dashboard", {
			from_date: from.value,
			to_date: to.value,
			number: number.value || null,
			team: team.value || null,
			rep: rep.value || null,
		});
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "The dashboard could not be loaded.";
	} finally {
		loading.value = false;
	}
}, 200);

watch([from, to, number, team, rep], load);
onMounted(async () => {
	if (!inbox.teams.length) loadTeams();
	load();
	reps.value = await call("periskope_connector.api.admin.pick", { doctype: "User", query: "" });
});
</script>

<style>
/* Chart tokens: the reference palette's categorical slots 1-2 (validated for colour-blind separation
   and contrast in both modes), a recessive grid and axis, and the bar track. */
.dashboard .viz-root {
	--series-1: #2a78d6;
	--series-2: #eb6834;
	--grid: #ecebe8;
	--axis: #d4d3cf;
	--track: #f1f0ed;
	--text-secondary: #52514e;
}
@media (prefers-color-scheme: dark) {
	:root:where(:not([data-theme="light"])) .dashboard .viz-root {
		--series-1: #3987e5;
		--series-2: #d95926;
		--grid: #2b2b29;
		--axis: #3a3a37;
		--track: #2b2b29;
		--text-secondary: #c3c2b7;
	}
}
:root[data-theme="dark"] .dashboard .viz-root {
	--series-1: #3987e5;
	--series-2: #d95926;
	--grid: #2b2b29;
	--axis: #3a3a37;
	--track: #2b2b29;
	--text-secondary: #c3c2b7;
}
</style>
