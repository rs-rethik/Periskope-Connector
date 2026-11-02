<template>
	<Dialog v-model="show" :options="{ size: '4xl' }">
		<template #body>
			<div class="flex max-h-[85vh] flex-col">
				<!-- header -->
				<div class="flex items-start gap-3 border-b border-outline-gray-1 px-5 pb-4 pt-5">
					<InitialsAvatar size="lg" :name="chat?.title || ''" />
					<div class="min-w-0 flex-1">
						<div class="truncate text-xl font-semibold text-ink-gray-9">
							{{ view?.lead?.lead_name || chat?.title }}
						</div>
						<div class="truncate text-sm text-ink-gray-6">
							<span v-if="view?.lead?.organization"
								>{{ view.lead.organization }} ·
							</span>
							<span v-if="chat?.contact_number">+{{ chat.contact_number }}</span>
						</div>
						<div v-if="view" class="mt-2 flex flex-wrap gap-1.5">
							<a
								v-if="view.lead"
								:href="view.lead.route"
								target="_blank"
								class="cp-chip bg-surface-gray-2 text-ink-gray-8 hover:bg-surface-gray-3"
							>
								Lead · {{ view.lead.status }}
								<LucideExternalLink class="size-3" />
							</a>
							<a
								v-for="c in view.customers"
								:key="c.name"
								:href="c.route"
								target="_blank"
								class="cp-chip bg-surface-green-1 text-ink-green-8 hover:bg-surface-green-2"
							>
								<LucideBuilding2 class="size-3" />Customer · {{ c.name }}
							</a>
						</div>
					</div>
					<Button
						variant="ghost"
						tooltip="Close"
						aria-label="Close"
						@click="show = false"
					>
						<template #icon><LucideX class="size-4" /></template>
					</Button>
				</div>

				<div v-if="loading && !view" class="flex justify-center p-10">
					<LoadingIndicator class="size-6" />
				</div>
				<div v-else-if="error" class="p-8 text-center text-sm text-ink-gray-6">
					{{ error }}
				</div>
				<template v-else-if="view">
					<!-- the numbers -->
					<div class="grid grid-cols-2 gap-2.5 px-5 pt-4 sm:grid-cols-4">
						<button
							v-for="t in tiles"
							:key="t.key"
							class="rounded-xl border p-3 text-left transition hover:shadow-sm"
							:class="
								tab === t.key
									? 'border-outline-gray-3 bg-surface-base shadow-sm'
									: 'border-outline-gray-1 bg-surface-gray-1'
							"
							@click="t.key !== 'value' && (tab = t.key)"
						>
							<div class="flex items-center gap-1.5 text-xs text-ink-gray-6">
								<component :is="t.icon" class="size-3.5" :class="t.tone" />{{
									t.label
								}}
							</div>
							<div class="mt-1 text-lg font-semibold tabular-nums text-ink-gray-9">
								{{ t.value }}
							</div>
							<div class="truncate text-xs text-ink-gray-5">{{ t.hint }}</div>
						</button>
					</div>

					<!-- tabs -->
					<div class="px-5 pt-4">
						<div class="inline-flex gap-0.5 rounded-full bg-surface-gray-2 p-1">
							<button
								v-for="t in tabs"
								:key="t.key"
								class="flex items-center gap-1.5 rounded-full px-3 py-1 text-sm transition"
								:class="
									tab === t.key
										? 'bg-surface-base font-medium text-ink-gray-9 shadow-sm'
										: 'text-ink-gray-6 hover:text-ink-gray-8'
								"
								@click="tab = t.key"
							>
								{{ t.label }}
								<span
									class="rounded-full bg-surface-gray-3 px-1.5 text-xs tabular-nums text-ink-gray-7"
									>{{ t.count }}</span
								>
							</button>
						</div>
					</div>

					<div class="min-h-0 flex-1 overflow-y-auto px-5 pb-5 pt-3">
						<!-- deals -->
						<template v-if="tab === 'deals'">
							<div v-if="!view.deals.length" class="cp-empty">No deals yet.</div>
							<div class="flex flex-col gap-2">
								<a
									v-for="d in view.deals"
									:key="d.name"
									:href="d.route"
									target="_blank"
									class="flex items-center gap-3 rounded-xl border p-3 transition hover:border-outline-gray-3 hover:shadow-sm"
									:class="
										d.name === view.record.name
											? 'border-outline-green-1 bg-surface-green-1'
											: 'border-outline-gray-1 bg-surface-base'
									"
								>
									<span
										class="grid size-9 shrink-0 place-items-center rounded-lg bg-surface-violet-1 text-ink-violet-7"
									>
										<LucideHandshake class="size-4" />
									</span>
									<span class="min-w-0 flex-1">
										<span class="flex items-center gap-2">
											<span class="truncate font-medium text-ink-gray-9">{{
												d.organization || d.lead_name || d.name
											}}</span>
											<span
												v-if="d.name === view.record.name"
												class="cp-chip bg-surface-green-2 text-ink-green-8"
												>This chat</span
											>
										</span>
										<span class="block truncate text-xs text-ink-gray-5">
											{{ d.name }} · {{ person(d.deal_owner) || "No owner" }}
											<template v-if="d.expected_closure_date">
												· closes
												{{ date(d.expected_closure_date) }}</template
											>
										</span>
									</span>
									<span class="flex shrink-0 flex-col items-end gap-1">
										<span class="cp-chip bg-surface-gray-2 text-ink-gray-8">
											<span
												class="size-2 rounded-full"
												:style="{ background: dot(d.color) }"
											/>{{ d.status }}
										</span>
										<span
											v-if="d.value"
											class="text-sm font-semibold tabular-nums text-ink-gray-8"
											>{{ money(d.value, d.currency) }}</span
										>
									</span>
								</a>
							</div>
						</template>

						<!-- quotations -->
						<template v-else-if="tab === 'quotations'">
							<div v-if="view.quotations === null" class="cp-empty">
								You don't have access to quotations.
							</div>
							<div v-else-if="!view.quotations.length" class="cp-empty">
								No quotations for this customer yet.
							</div>
							<div
								v-else
								class="overflow-hidden rounded-xl border border-outline-gray-1"
							>
								<table class="w-full text-sm">
									<thead
										class="bg-surface-gray-1 text-left text-xs text-ink-gray-5"
									>
										<tr>
											<th class="px-3 py-2 font-medium">Quotation</th>
											<th class="px-3 py-2 font-medium">Date</th>
											<th class="px-3 py-2 font-medium">Valid till</th>
											<th class="px-3 py-2 text-right font-medium">
												Amount
											</th>
											<th class="px-3 py-2 font-medium">Status</th>
										</tr>
									</thead>
									<tbody>
										<tr
											v-for="q in view.quotations"
											:key="q.name"
											class="cursor-pointer border-t border-outline-gray-1 hover:bg-surface-gray-1"
											@click="open(q.route)"
										>
											<td class="px-3 py-2 font-medium text-ink-gray-8">
												{{ q.name }}
											</td>
											<td class="px-3 py-2 text-ink-gray-6">
												{{ date(q.transaction_date) }}
											</td>
											<td class="px-3 py-2 text-ink-gray-6">
												{{ date(q.valid_till) }}
											</td>
											<td
												class="px-3 py-2 text-right tabular-nums text-ink-gray-8"
											>
												{{ money(q.grand_total, q.currency) }}
											</td>
											<td class="px-3 py-2">
												<span
													class="cp-chip"
													:class="statusTone(q.status)"
													>{{ q.status }}</span
												>
											</td>
										</tr>
									</tbody>
								</table>
							</div>
						</template>

						<!-- sales orders -->
						<template v-else>
							<div v-if="view.orders === null" class="cp-empty">
								You don't have access to sales orders.
							</div>
							<div v-else-if="!view.orders.length" class="cp-empty">
								No sales orders for this customer yet.
							</div>
							<div
								v-else
								class="overflow-hidden rounded-xl border border-outline-gray-1"
							>
								<table class="w-full text-sm">
									<thead
										class="bg-surface-gray-1 text-left text-xs text-ink-gray-5"
									>
										<tr>
											<th class="px-3 py-2 font-medium">Sales order</th>
											<th class="px-3 py-2 font-medium">Date</th>
											<th class="px-3 py-2 font-medium">Delivery</th>
											<th class="px-3 py-2 text-right font-medium">
												Amount
											</th>
											<th class="px-3 py-2 font-medium">
												Delivered · Billed
											</th>
											<th class="px-3 py-2 font-medium">Status</th>
										</tr>
									</thead>
									<tbody>
										<tr
											v-for="o in view.orders"
											:key="o.name"
											class="cursor-pointer border-t border-outline-gray-1 hover:bg-surface-gray-1"
											@click="open(o.route)"
										>
											<td class="px-3 py-2 font-medium text-ink-gray-8">
												{{ o.name }}
											</td>
											<td class="px-3 py-2 text-ink-gray-6">
												{{ date(o.transaction_date) }}
											</td>
											<td class="px-3 py-2 text-ink-gray-6">
												{{ date(o.delivery_date) }}
											</td>
											<td
												class="px-3 py-2 text-right tabular-nums text-ink-gray-8"
											>
												{{ money(o.grand_total, o.currency) }}
											</td>
											<td class="px-3 py-2">
												<div class="flex w-28 flex-col gap-1">
													<div
														v-for="bar in [
															{
																v: o.per_delivered,
																c: 'bg-surface-green-6',
															},
															{
																v: o.per_billed,
																c: 'bg-surface-blue-6',
															},
														]"
														:key="bar.c"
														class="h-1.5 overflow-hidden rounded-full bg-surface-gray-3"
													>
														<div
															class="h-full rounded-full"
															:class="bar.c"
															:style="{
																width: `${Math.min(
																	100,
																	bar.v || 0
																)}%`,
															}"
														/>
													</div>
													<span
														class="text-[11px] tabular-nums text-ink-gray-5"
														>{{ Math.round(o.per_delivered || 0) }}% ·
														{{ Math.round(o.per_billed || 0) }}%</span
													>
												</div>
											</td>
											<td class="px-3 py-2">
												<span
													class="cp-chip"
													:class="statusTone(o.status)"
													>{{ o.status }}</span
												>
											</td>
										</tr>
									</tbody>
								</table>
							</div>
						</template>
					</div>
				</template>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import dayjs from "dayjs";
import { Button, Dialog, LoadingIndicator, call } from "frappe-ui";
import LucideX from "~icons/lucide/x";
import LucideExternalLink from "~icons/lucide/external-link";
import LucideBuilding2 from "~icons/lucide/building-2";
import LucideHandshake from "~icons/lucide/handshake";
import LucideFileText from "~icons/lucide/file-text";
import LucidePackage from "~icons/lucide/package";
import LucideIndianRupee from "~icons/lucide/indian-rupee";
import InitialsAvatar from "@/components/InitialsAvatar.vue";
import { errorText } from "@/utils/errors";

// The customer's full picture: the lead, all its deals, and their quotations and sales orders.
const show = defineModel({ type: Boolean, default: false });
const props = defineProps({ chat: { type: Object, default: null } });

const view = ref(null);
const loading = ref(false);
const error = ref("");
const tab = ref("deals");

async function load() {
	if (!props.chat) return;
	loading.value = true;
	error.value = "";
	try {
		view.value = await call("periskope_connector.api.crm_panel.full_view", {
			chat_name: props.chat.name,
		});
	} catch (e) {
		error.value = errorText(e, "Could not load the full view.");
	} finally {
		loading.value = false;
	}
}
watch(show, (open) => open && load());
watch(
	() => props.chat?.name,
	() => {
		view.value = null;
		tab.value = "deals";
	}
);

const sum = (rows) => (rows || []).reduce((total, r) => total + (r.grand_total || 0), 0);
const OPEN_DEAL = (d) => !d.closed; // the status's type is Won or Lost

const tabs = computed(() => [
	{ key: "deals", label: "Deals", count: view.value?.deals.length || 0 },
	{ key: "quotations", label: "Quotations", count: view.value?.quotations?.length || 0 },
	{ key: "orders", label: "Sales orders", count: view.value?.orders?.length || 0 },
]);

const tiles = computed(() => {
	const v = view.value || {};
	const deals = v.deals || [];
	const currency = deals[0]?.currency || v.quotations?.[0]?.currency || v.orders?.[0]?.currency;
	const pipeline = deals.filter(OPEN_DEAL).reduce((t, d) => t + (d.value || 0), 0);
	return [
		{
			key: "deals",
			label: "Deals",
			value: deals.length,
			hint: `${deals.filter(OPEN_DEAL).length} open`,
			icon: LucideHandshake,
			tone: "text-ink-violet-7",
		},
		{
			key: "value",
			label: "Open pipeline",
			value: money(pipeline, currency),
			hint: "Value of the open deals",
			icon: LucideIndianRupee,
			tone: "text-ink-green-7",
		},
		{
			key: "quotations",
			label: "Quotations",
			value:
				v.quotations === null
					? "—"
					: `${(v.quotations || []).length}${v.more?.quotations ? "+" : ""}`,
			hint: v.quotations?.length
				? `${money(sum(v.quotations), currency)}${
						v.more?.quotations ? " · latest 50" : ""
				  }`
				: "None yet",
			icon: LucideFileText,
			tone: "text-ink-blue-7",
		},
		{
			key: "orders",
			label: "Sales orders",
			value:
				v.orders === null ? "—" : `${(v.orders || []).length}${v.more?.orders ? "+" : ""}`,
			hint: v.orders?.length
				? `${money(sum(v.orders), currency)}${v.more?.orders ? " · latest 50" : ""}`
				: "None yet",
			icon: LucidePackage,
			tone: "text-ink-amber-8",
		},
	];
});

function money(value, currency) {
	try {
		return new Intl.NumberFormat("en-IN", {
			style: "currency",
			currency: currency || "INR",
			maximumFractionDigits: 0,
		}).format(value || 0);
	} catch {
		return String(Math.round(value || 0));
	}
}

const date = (value) => (value ? dayjs(value).format("D MMM YYYY") : "—");

function person(user) {
	return (user || "")
		.split("@")[0]
		.replace(/[._]/g, " ")
		.replace(/\b\w/g, (c) => c.toUpperCase());
}

// CRM status colours are names; show them as a dot.
const COLORS = {
	gray: "#9ca3af",
	blue: "#3b82f6",
	green: "#22c55e",
	red: "#ef4444",
	orange: "#f97316",
	amber: "#f59e0b",
	yellow: "#eab308",
	cyan: "#06b6d4",
	teal: "#14b8a6",
	violet: "#8b5cf6",
	pink: "#ec4899",
	purple: "#a855f7",
	black: "#374151",
};
const dot = (color) => COLORS[color] || COLORS.gray;

function statusTone(status) {
	const s = (status || "").toLowerCase();
	if (/(cancel|lost|expired)/.test(s)) return "bg-surface-red-1 text-ink-red-7";
	if (/(complete|ordered|closed)/.test(s)) return "bg-surface-green-1 text-ink-green-8";
	if (/(draft)/.test(s)) return "bg-surface-gray-2 text-ink-gray-7";
	return "bg-surface-amber-1 text-ink-amber-8";
}

function open(url) {
	window.open(url, "_blank");
}
</script>
