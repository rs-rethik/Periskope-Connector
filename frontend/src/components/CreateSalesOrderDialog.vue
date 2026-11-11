<template>
	<Dialog
		v-model="show"
		:options="{ title: created ? 'Sales order created' : 'Create sales order', size: '2xl' }"
	>
		<template #body-content>
			<!-- done -->
			<div v-if="created" class="flex items-center gap-3">
				<span
					class="grid size-10 place-items-center rounded-xl bg-surface-blue-2 text-ink-blue-8"
				>
					<LucideShoppingCart class="size-5" />
				</span>
				<div>
					<div class="text-base font-semibold text-ink-gray-9">
						{{ created.name }} (Draft)
					</div>
					<div class="text-sm text-ink-gray-6">
						The message is on the order. Fill in the rest (payment terms, sales team…)
						in ERPNext and submit it there.
					</div>
				</div>
			</div>

			<div v-else class="flex flex-col gap-3">
				<!-- the message it comes from -->
				<div class="rounded-xl border border-outline-gray-1 bg-surface-gray-1 p-3">
					<div
						class="mb-1 flex items-center gap-1.5 text-xs font-semibold text-ink-gray-6"
					>
						<LucideMessageSquareQuote class="size-3.5" />
						{{
							chat?.is_group
								? `${message?.sender_label || "Customer"}'s message`
								: "Customer's message"
						}}
						<span class="font-normal text-ink-gray-5">· {{ when }}</span>
					</div>
					<div class="line-clamp-3 whitespace-pre-wrap text-sm text-ink-gray-8">
						{{ preview }}
					</div>
				</div>

				<div v-if="loading" class="flex justify-center py-4">
					<LoadingIndicator class="size-5" />
				</div>
				<template v-else-if="defaults">
					<div
						v-if="!defaults.can_create"
						class="rounded-lg bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-8"
					>
						You don't have permission to create sales orders.
					</div>

					<!-- customer -->
					<div>
						<div class="mb-1 text-xs text-ink-gray-5">Customer</div>
						<div
							v-if="customer"
							class="flex items-center gap-2 rounded-lg bg-surface-gray-2 px-2.5 py-1.5 text-sm"
						>
							<LucideBuilding2 class="size-3.5 text-ink-gray-5" />
							<span class="truncate text-ink-gray-9">{{
								customer.label || customer.value
							}}</span>
							<Button
								class="ml-auto"
								size="sm"
								variant="ghost"
								label="Change"
								@click="customer = null"
							/>
						</div>
						<template v-else>
							<div
								v-if="defaults.customers.length > 1"
								class="mb-1.5 flex flex-wrap gap-1"
							>
								<Button
									v-for="c in defaults.customers"
									:key="c"
									size="sm"
									:label="c"
									@click="customer = { value: c }"
								/>
							</div>
							<RecordSearch
								doctype="Customer"
								placeholder="Search customers"
								@pick="customer = $event"
							/>
						</template>
						<p v-if="!defaults.customers.length" class="mt-1 text-xs text-ink-gray-5">
							{{
								defaults.deal
									? `Deal ${defaults.deal} has no ERPNext customer yet: pick one.`
									: "This chat has no deal: pick the customer."
							}}
						</p>
					</div>

					<!-- items -->
					<div>
						<div class="mb-1 text-xs text-ink-gray-5">Items</div>
						<div
							v-if="lines.length"
							class="mb-2 overflow-hidden rounded-lg border border-outline-gray-1"
						>
							<div
								class="grid grid-cols-[1fr_5rem_6.5rem_6rem_2rem] gap-2 bg-surface-gray-1 px-2.5 py-1.5 text-xs text-ink-gray-5"
							>
								<span>Item</span><span>Qty</span><span>Rate</span
								><span class="text-right">Amount</span><span />
							</div>
							<div
								v-for="(l, i) in lines"
								:key="i"
								class="grid grid-cols-[1fr_5rem_6.5rem_6rem_2rem] items-center gap-2 border-t border-outline-gray-1 px-2.5 py-1.5"
							>
								<div class="min-w-0">
									<div class="truncate text-sm text-ink-gray-9">
										{{ l.label }}
									</div>
									<div class="truncate text-xs text-ink-gray-5">
										{{ l.item_code }}{{ l.uom ? ` · ${l.uom}` : "" }}
									</div>
								</div>
								<TextInput v-model="l.qty" type="number" min="0" />
								<TextInput v-model="l.rate" type="number" min="0" />
								<span class="text-right text-sm tabular-nums text-ink-gray-8">{{
									amount(l)
								}}</span>
								<Tooltip text="Remove"
									><button
										class="grid size-7 place-items-center rounded text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-red-6"
										aria-label="Remove the item"
										@click="lines.splice(i, 1)"
									>
										<LucideX class="size-4" /></button
								></Tooltip>
							</div>
							<div
								class="flex justify-end gap-2 border-t border-outline-gray-1 px-2.5 py-1.5 text-sm"
							>
								<span class="text-ink-gray-5">Total</span>
								<span class="font-semibold tabular-nums text-ink-gray-9">{{
									total
								}}</span>
							</div>
						</div>
						<RecordSearch doctype="Item" placeholder="Add an item" @pick="addItem" />
					</div>

					<div class="grid grid-cols-2 gap-3">
						<FormControl v-model="deliveryDate" type="date" label="Delivery date" />
						<FormControl
							v-if="defaults.series.length > 1"
							v-model="series"
							type="select"
							label="Naming series"
							:options="defaults.series.map((s) => ({ label: s, value: s }))"
						/>
					</div>
					<FormControl
						v-model="note"
						type="textarea"
						label="Note (optional)"
						:rows="2"
						placeholder="Anything to add to the message on the order"
					/>
					<p class="text-xs text-ink-gray-5">
						Saved as a Draft in ERPNext{{
							defaults.deal ? `, linked to deal ${defaults.deal}` : ""
						}}. The rest (payment terms, sales team…) is filled in ERPNext before
						submitting.
					</p>
				</template>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<div class="flex w-full gap-2">
				<template v-if="created">
					<Button class="flex-1" label="Close" @click="show = false" />
					<Button
						class="flex-1"
						variant="solid"
						label="Open in ERPNext"
						@click="openOrder"
					>
						<template #suffix><LucideExternalLink class="size-3.5" /></template>
					</Button>
				</template>
				<Button
					v-else
					class="w-full"
					variant="solid"
					label="Create draft sales order"
					:loading="busy"
					:disabled="!ready"
					@click="create"
				/>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import dayjs from "dayjs";
import {
	Button,
	Dialog,
	ErrorMessage,
	FormControl,
	LoadingIndicator,
	TextInput,
	Tooltip,
	call,
} from "frappe-ui";
import LucideShoppingCart from "~icons/lucide/shopping-cart";
import LucideExternalLink from "~icons/lucide/external-link";
import LucideMessageSquareQuote from "~icons/lucide/message-square-quote";
import LucideBuilding2 from "~icons/lucide/building-2";
import LucideX from "~icons/lucide/x";
import RecordSearch from "@/components/RecordSearch.vue";
import { errorText } from "@/utils/errors";

// "Create sales order" from a customer's message (api/sales_order.py): a Draft in ERPNext with the
// customer, items, delivery date and series; the message and the deal stay linked to it.
const show = defineModel({ type: Boolean, default: false });
const props = defineProps({
	chat: { type: Object, default: null },
	message: { type: Object, default: null },
});
const emit = defineEmits(["created"]);

const defaults = ref(null);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const created = ref(null);
const customer = ref(null);
const lines = ref([]);
const deliveryDate = ref("");
const series = ref("");
const note = ref("");

const preview = computed(() => {
	const m = props.message;
	return m ? m.body || m.transcript || `[${m.message_type || "message"}]` : "";
});
const when = computed(() =>
	props.message ? dayjs(props.message.message_timestamp).format("D MMM, h:mm A") : ""
);
const money = (v) =>
	new Intl.NumberFormat(undefined, {
		maximumFractionDigits: 2,
		minimumFractionDigits: 2,
	}).format(v || 0);
const amount = (l) => money((Number(l.qty) || 0) * (Number(l.rate) || 0));
const total = computed(() =>
	money(lines.value.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.rate) || 0), 0))
);
const ready = computed(
	() =>
		defaults.value?.can_create &&
		customer.value &&
		lines.value.length &&
		lines.value.every((l) => Number(l.qty) > 0) &&
		deliveryDate.value &&
		!busy.value
);

function addItem(r) {
	const same = lines.value.find((l) => l.item_code === r.value);
	if (same) same.qty = (Number(same.qty) || 0) + 1;
	else
		lines.value.push({
			item_code: r.value,
			label: r.label || r.value,
			uom: r.uom,
			qty: 1,
			rate: r.rate || 0,
		});
}

// (run at once too: the dialog is loaded on demand and may mount already open)
watch(
	show,
	async (open) => {
		if (!open || !props.chat || !props.message) return;
		created.value = null;
		error.value = "";
		lines.value = [];
		note.value = "";
		customer.value = null;
		loading.value = true;
		try {
			defaults.value = await call("periskope_connector.api.sales_order.so_defaults", {
				chat_name: props.chat.name,
				message: props.message.name,
			});
			if (defaults.value.customer) customer.value = { value: defaults.value.customer };
			deliveryDate.value = defaults.value.delivery_date;
			series.value = defaults.value.naming_series || "";
		} catch (e) {
			defaults.value = null;
			error.value = errorText(e, "Could not open the form.");
		} finally {
			loading.value = false;
		}
	},
	{ immediate: true }
);

async function create() {
	busy.value = true;
	error.value = "";
	try {
		created.value = await call(
			"periskope_connector.api.sales_order.create_sales_order_from_message",
			{
				chat_name: props.chat.name,
				message: props.message.name,
				customer: customer.value.value,
				items: lines.value.map((l) => ({
					item_code: l.item_code,
					qty: l.qty,
					rate: l.rate,
				})),
				delivery_date: deliveryDate.value,
				naming_series: series.value || null,
				note: note.value,
			}
		);
		emit("created", created.value);
	} catch (e) {
		error.value = errorText(e, "Could not create the sales order.");
	} finally {
		busy.value = false;
	}
}

function openOrder() {
	if (created.value?.route) window.open(created.value.route, "_blank");
}
</script>
