<template>
	<div class="grid gap-3 py-2 md:grid-cols-2">
		<div
			v-for="n in rows"
			:key="n.name"
			role="button"
			tabindex="0"
			class="group flex cursor-pointer flex-col gap-3 rounded-xl border bg-surface-base p-4 transition hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3"
			:class="linked(n) || !n.is_active ? 'border-outline-gray-2' : 'border-outline-red-3'"
			@click="emit('edit', n)"
			@keydown.enter.self="emit('edit', n)"
		>
			<!-- name and connection -->
			<div class="flex items-start gap-3">
				<span
					class="grid size-10 shrink-0 place-items-center rounded-full"
					:class="
						n.source === 'Meta'
							? 'bg-surface-blue-2 text-ink-blue-7'
							: 'bg-surface-green-2 text-ink-green-8'
					"
				>
					<LucidePhone class="size-5" />
				</span>
				<div class="min-w-0 flex-1">
					<div class="flex items-center gap-2">
						<span class="min-w-0 truncate text-base font-semibold text-ink-gray-9">
							{{ n.display_name || n.phone_name || pretty(n.number) }}
						</span>
						<span
							class="ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
							:class="
								linked(n)
									? 'bg-surface-green-2 text-ink-green-8'
									: 'bg-surface-red-2 text-ink-red-7'
							"
						>
							<span
								class="size-1.5 rounded-full"
								:class="linked(n) ? 'bg-surface-green-6' : 'bg-surface-red-6'"
							/>
							{{ stateLabel(n) }}
						</span>
					</div>
					<div class="mt-0.5 text-sm text-ink-gray-6">
						{{ pretty(n.number) }}
						<span class="text-ink-gray-4">·</span>
						{{ n.source || "Periskope" }}
						<template v-if="!n.is_active">
							<span class="text-ink-gray-4">·</span> Off
						</template>
					</div>
				</div>
			</div>

			<!-- who it goes to -->
			<dl class="grid grid-cols-[6.5rem_1fr] gap-x-2 gap-y-1 text-sm text-ink-gray-8">
				<dt class="text-ink-gray-5">Team</dt>
				<dd class="truncate text-ink-gray-8">{{ n.default_team || "—" }}</dd>
				<dt class="text-ink-gray-5">Rep</dt>
				<dd class="truncate text-ink-gray-8">{{ n.default_assignee || "—" }}</dd>
				<dt class="text-ink-gray-5">Last message</dt>
				<dd class="truncate text-ink-gray-8">
					{{ n.last_message_on ? fromNow(n.last_message_on) : "—" }}
				</dd>
			</dl>
			<p
				v-if="!linked(n) && n.disconnected_since"
				class="-mt-1 text-xs font-medium text-ink-red-7"
			>
				Disconnected {{ fromNow(n.disconnected_since) }}
			</p>

			<!-- the send queue in Periskope (admins) -->
			<div
				v-if="admin && n.source !== 'Meta' && queues[n.name]"
				class="-mt-1 flex items-center gap-2 rounded-lg bg-surface-gray-1 px-2 py-1.5 text-xs text-ink-gray-8"
				@click.stop
			>
				<LucideListOrdered class="size-3.5 shrink-0 text-ink-gray-5" />
				<span class="min-w-0 flex-1 text-ink-gray-7">
					Send queue:
					<template v-if="queues[n.name].error">{{ queues[n.name].error }}</template>
					<template v-else>
						{{ queues[n.name].pending + queues[n.name].active }} waiting
						<span v-if="queues[n.name].failed" class="text-ink-red-7">
							· {{ queues[n.name].failed }} failed
						</span>
						<span v-if="!queues[n.name].running" class="text-ink-amber-8">
							· paused</span
						>
					</template>
				</span>
				<Button
					v-if="queues[n.name].pending"
					size="sm"
					variant="ghost"
					theme="red"
					label="Clear waiting"
					:loading="clearing === n.name"
					@click.stop="clear(n)"
				/>
			</div>

			<!-- actions -->
			<div class="mt-auto flex items-center gap-2 border-t border-outline-gray-1 pt-3">
				<Button
					v-if="admin && n.source !== 'Meta'"
					size="sm"
					:variant="linked(n) ? 'subtle' : 'solid'"
					:label="linked(n) ? 'Re-link' : 'Connect'"
					@click.stop="emit('link', n)"
				>
					<template #prefix>
						<LucideQrCode class="size-3.5" />
					</template>
				</Button>
				<span v-else-if="n.source === 'Meta'" class="text-xs text-ink-gray-5">
					Linked in Meta
				</span>
				<Button
					class="ml-auto"
					size="sm"
					variant="ghost"
					label="Edit"
					@click.stop="emit('edit', n)"
				>
					<template #prefix><LucidePencil class="size-3.5" /></template>
				</Button>
			</div>
		</div>
	</div>
</template>

<script setup>
import { onMounted, ref, watch } from "vue";
import { Button, call, toast } from "frappe-ui";
import LucideListOrdered from "~icons/lucide/list-ordered";
import { ask } from "@/utils/confirm";
import { errorText, showError } from "@/utils/errors";
import LucidePhone from "~icons/lucide/phone";
import LucideQrCode from "~icons/lucide/qr-code";
import LucidePencil from "~icons/lucide/pencil";
import { fromNow } from "@/utils/format";

// The WhatsApp numbers as cards (Settings > WhatsApp numbers): few numbers, and whether each is
// connected matters most. A click opens the same editor as the table did.
const props = defineProps({
	rows: { type: Array, default: () => [] },
	admin: { type: Boolean, default: false },
});
const emit = defineEmits(["edit", "link"]);

// Each Periskope number's send queue (api/phones.queue), for admins: what is waiting or failed, and
// whether it runs (it pauses while the number is disconnected). "Clear waiting" empties it.
const queues = ref({});
const clearing = ref("");

async function loadQueues() {
	if (!props.admin) return;
	for (const n of props.rows.filter((r) => r.source !== "Meta")) {
		try {
			queues.value = {
				...queues.value,
				[n.name]: await call("periskope_connector.api.phones.queue", { number: n.name }),
			};
		} catch (e) {
			queues.value = { ...queues.value, [n.name]: { error: errorText(e, "not available") } };
		}
	}
}

async function clear(n) {
	const q = queues.value[n.name];
	const ok = await ask(
		`${q.pending} ${q.pending === 1 ? "message" : "messages"} waiting to go out from ${
			n.display_name || n.number
		} will not be sent. Those already with WhatsApp are not affected. Inbox replies among them are marked Failed, so they can be sent again with Retry.`,
		{ title: "Clear the waiting messages?", confirmLabel: "Clear", danger: true }
	);
	if (!ok) return;
	clearing.value = n.name;
	try {
		const r = await call("periskope_connector.api.phones.purge_queue", { number: n.name });
		queues.value = { ...queues.value, [n.name]: r.queue };
		toast.success(
			r.cancelled
				? `Cleared; ${r.cancelled} inbox ${
						r.cancelled === 1 ? "reply" : "replies"
				  } marked Failed`
				: "Cleared"
		);
	} catch (e) {
		showError(e, "The send queue could not be cleared.");
	} finally {
		clearing.value = "";
	}
}

onMounted(loadQueues);
watch(() => props.rows.length, loadQueues);

const linked = (n) => !!n.is_ready;

function stateLabel(n) {
	if (n.is_ready) return "Connected";
	const state = (n.wa_state || "").toUpperCase();
	if (state.startsWith("UNPAIRED") || state === "NOT LINKED" || !state) return "Not linked";
	return state.charAt(0) + state.slice(1).toLowerCase().replace(/_/g, " ");
}

// 919786387999 -> +91 97863 87999 (Indian numbers); others as +<digits>
function pretty(number) {
	const d = String(number || "").replace(/\D/g, "");
	if (d.length === 12 && d.startsWith("91")) return `+91 ${d.slice(2, 7)} ${d.slice(7)}`;
	return d ? `+${d}` : "";
}
</script>
