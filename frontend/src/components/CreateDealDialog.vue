<template>
	<Dialog
		v-model="show"
		:options="{ title: created ? 'Deal created' : 'Create deal from message' }"
	>
		<template #body-content>
			<!-- done -->
			<div v-if="created" class="flex flex-col items-start gap-3">
				<div class="flex items-center gap-3">
					<span
						class="grid size-10 place-items-center rounded-xl bg-surface-green-2 text-ink-green-8"
					>
						<LucideHandshake class="size-5" />
					</span>
					<div>
						<div class="text-base font-semibold text-ink-gray-9">
							{{ created.name }}
						</div>
						<div class="text-sm text-ink-gray-6">
							The message is on the deal, and this chat now follows it.
						</div>
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
					<div class="line-clamp-4 whitespace-pre-wrap text-sm text-ink-gray-8">
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
						You don't have permission to create deals.
					</div>
					<div class="grid grid-cols-2 gap-3">
						<FormControl v-model="form.first_name" label="Contact first name" />
						<FormControl v-model="form.last_name" label="Last name" />
					</div>
					<FormControl
						v-model="form.organization"
						label="Company"
						placeholder="Found or created in the CRM"
					/>
					<div class="grid grid-cols-2 gap-3">
						<FormControl
							v-model="form.status"
							type="select"
							label="Status"
							:options="
								defaults.statuses.map((s) => ({ label: s.value, value: s.value }))
							"
						/>
						<FormControl
							v-model="form.expected_closure_date"
							type="date"
							label="Expected closing"
						/>
					</div>
					<FormControl
						v-model="form.deal_value"
						type="number"
						label="Deal value (optional)"
						placeholder="0"
					/>
					<FormControl
						v-model="form.note"
						type="textarea"
						label="Note (optional)"
						placeholder="Anything to add to the message on the deal"
					/>
					<div class="text-xs text-ink-gray-5">
						<template v-if="chat?.is_group && defaults.for_phone"
							>For the sender, +{{ defaults.for_phone }}.
						</template>
						<template v-if="defaults.lead"
							>Linked to lead {{ defaults.lead }} (the lead stays as it
							is).</template
						>
						<template v-else
							>No lead found for this number; the deal stands alone.</template
						>
					</div>
				</template>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<div class="flex w-full gap-2">
				<template v-if="created">
					<Button class="flex-1" label="Close" @click="show = false" />
					<Button class="flex-1" variant="solid" label="Open deal" @click="openDeal">
						<template #suffix><LucideExternalLink class="size-3.5" /></template>
					</Button>
				</template>
				<Button
					v-else
					class="w-full"
					variant="solid"
					label="Create deal"
					:loading="busy"
					:disabled="!defaults || !defaults.can_create"
					@click="create"
				/>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import dayjs from "dayjs";
import { Button, Dialog, ErrorMessage, FormControl, LoadingIndicator, call } from "frappe-ui";
import LucideHandshake from "~icons/lucide/handshake";
import LucideExternalLink from "~icons/lucide/external-link";
import LucideMessageSquareQuote from "~icons/lucide/message-square-quote";
import { errorText } from "@/utils/errors";

// "Create deal" from one message: the deal keeps the message (a field and a note), the chat's lead
// stays as it is, and the chat follows the new deal.
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
const form = ref({});

const preview = computed(() => {
	const m = props.message;
	if (!m) return "";
	return m.body || m.transcript || `[${m.message_type || "message"}]`;
});
const when = computed(() =>
	props.message ? dayjs(props.message.message_timestamp).format("D MMM, h:mm A") : ""
);

// (run at once too: the dialog is loaded on demand and may mount already open)
watch(
	show,
	async (open) => {
		if (!open || !props.chat) return;
		created.value = null;
		error.value = "";
		loading.value = true;
		try {
			defaults.value = await call("periskope_connector.api.crm_panel.deal_defaults", {
				chat_name: props.chat.name,
				message: props.message?.name,
			});
			form.value = {
				first_name: defaults.value.first_name,
				last_name: defaults.value.last_name,
				organization: defaults.value.organization,
				status: defaults.value.status,
				expected_closure_date: dayjs().add(14, "day").format("YYYY-MM-DD"),
				deal_value: "",
				note: "",
			};
		} catch (e) {
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
		const ctx = await call("periskope_connector.api.crm_panel.create_deal_from_message", {
			chat_name: props.chat.name,
			message: props.message.name,
			...form.value,
			deal_value: form.value.deal_value || null,
		});
		created.value = ctx.created_deal;
		emit("created", ctx.created_deal);
	} catch (e) {
		error.value = errorText(e, "Could not create the deal.");
	} finally {
		busy.value = false;
	}
}

function openDeal() {
	if (created.value?.route) window.open(created.value.route, "_blank");
}
</script>
