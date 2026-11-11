<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="page-head">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Broadcasts</h1>
			<span class="text-sm text-ink-gray-5"
				>One message to many chats, sent by Periskope one by one.</span
			>
			<div class="ml-auto flex items-center gap-2">
				<Button
					variant="ghost"
					:loading="loading"
					tooltip="Refresh"
					aria-label="Refresh"
					@click="load"
				>
					<template #icon><LucideRefreshCw class="size-4" /></template>
				</Button>
				<Button variant="solid" label="New broadcast" @click="creating = true">
					<template #prefix><LucideMegaphone class="size-4" /></template>
				</Button>
			</div>
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto p-5">
			<div v-if="rows.length" class="grid gap-3 lg:grid-cols-2">
				<div
					v-for="b in rows"
					:key="b.name"
					class="flex flex-col gap-2 rounded-xl border border-outline-gray-2 bg-surface-base p-4"
				>
					<div class="flex items-start gap-2">
						<div class="min-w-0 flex-1">
							<div class="truncate text-sm font-semibold text-ink-gray-9">
								{{ b.title }}
							</div>
							<div class="text-xs text-ink-gray-5">
								{{ numberLabel(b.whatsapp_number) }} ·
								{{ b.recipient_count }} chats · by {{ b.owner_name }} ·
								{{ when(b.scheduled_at || b.creation) }}
							</div>
						</div>
						<span
							class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
							:class="STATUS[b.status]"
						>
							{{ b.status }}
						</span>
					</div>
					<p class="line-clamp-3 whitespace-pre-wrap text-sm text-ink-gray-8">
						<LucidePaperclip
							v-if="b.file"
							class="mr-1 inline size-3.5 text-ink-gray-5"
						/>{{ b.message }}
					</p>
					<!-- progress -->
					<div class="flex h-2 overflow-hidden rounded-full bg-surface-gray-2">
						<div
							v-for="p in parts(b)"
							:key="p.key"
							:class="p.class"
							:style="{ width: `${(p.n / Math.max(1, b.recipient_count)) * 100}%` }"
						/>
					</div>
					<div class="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-gray-6">
						<span v-for="p in parts(b)" :key="p.key" class="flex items-center gap-1">
							<span class="size-2 rounded-full" :class="p.class" />{{ p.n }}
							{{ p.label }}
						</span>
						<span v-if="b.queued" class="flex items-center gap-1">
							<span class="size-2 rounded-full bg-surface-gray-4" />{{ b.queued }}
							waiting
						</span>
					</div>
					<p v-if="failed[b.name]?.length" class="text-xs text-ink-red-7">
						Not sent to: {{ failed[b.name].join(", ") }}
					</p>
					<div class="flex items-center gap-2">
						<span class="text-xs text-ink-gray-5">
							{{
								b.checked_on
									? `Checked ${fromNow(b.checked_on)}`
									: "Not checked yet"
							}}
						</span>
						<Button
							class="ml-auto"
							size="sm"
							label="Check progress"
							:loading="busy === b.name"
							@click="check(b)"
						/>
						<Button
							v-if="['Scheduled', 'Sending'].includes(b.status)"
							size="sm"
							theme="red"
							variant="subtle"
							label="Stop"
							:loading="busy === 'stop' + b.name"
							@click="stop(b)"
						/>
					</div>
				</div>
			</div>
			<p v-else-if="!loading" class="py-16 text-center text-sm text-ink-gray-5">
				No broadcast yet. Use New broadcast to send one message to many chats.
			</p>
		</div>
		<NewBroadcastDialog v-model="creating" @sent="added" />
	</div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { Button, call, toast } from "frappe-ui";
import LucideRefreshCw from "~icons/lucide/refresh-cw";
import LucideMegaphone from "~icons/lucide/megaphone";
import LucidePaperclip from "~icons/lucide/paperclip";
import NewBroadcastDialog from "@/components/NewBroadcastDialog.vue";
import { numberLabel } from "@/boot";
import { ask } from "@/utils/confirm";
import { showError } from "@/utils/errors";
import { fromNow } from "@/utils/format";
import { when } from "@/utils/schedule";

// Broadcasts (managers): api/broadcast.py. Progress is read from Periskope's message queue.
const API = "periskope_connector.api.broadcast.";
const STATUS = {
	Scheduled: "bg-surface-blue-2 text-ink-blue-7",
	Sending: "bg-surface-amber-2 text-ink-amber-8",
	Done: "bg-surface-green-2 text-ink-green-8",
	Stopped: "bg-surface-gray-2 text-ink-gray-6",
	Cancelled: "bg-surface-gray-2 text-ink-gray-6",
};
const rows = ref([]);
const failed = ref({});
const loading = ref(false);
const busy = ref("");
const creating = ref(false);

const parts = (b) =>
	[
		{ key: "read", n: b.read, label: "read", class: "bg-surface-blue-5" },
		{ key: "delivered", n: b.delivered, label: "delivered", class: "bg-surface-green-5" },
		{ key: "sent", n: b.sent, label: "sent", class: "bg-surface-green-3" },
		{ key: "failed", n: b.failed, label: "failed", class: "bg-surface-red-5" },
	].filter((p) => p.n);

async function load() {
	loading.value = true;
	try {
		rows.value = await call(API + "broadcasts");
	} catch (e) {
		showError(e, "Could not load the broadcasts.");
	} finally {
		loading.value = false;
	}
	for (const b of rows.value.filter((x) => ["Scheduled", "Sending"].includes(x.status)))
		check(b, true);
}

function replace(b) {
	rows.value = rows.value.map((x) => (x.name === b.name ? b : x));
}

async function check(b, quiet = false) {
	busy.value = b.name;
	try {
		const r = await call(API + "progress", { broadcast: b.name });
		replace(r.broadcast);
		failed.value = { ...failed.value, [b.name]: r.failed };
	} catch (e) {
		if (!quiet) showError(e, "Could not read the progress from Periskope.");
	} finally {
		busy.value = "";
	}
}

async function stop(b) {
	const ok = await ask(
		"The messages not sent yet are cancelled and their credits returned. Those already sent stay.",
		{
			title: "Stop this broadcast?",
			confirmLabel: "Stop",
			danger: true,
		}
	);
	if (!ok) return;
	busy.value = "stop" + b.name;
	try {
		replace(await call(API + "stop", { broadcast: b.name }));
		toast.success("Broadcast stopped");
	} catch (e) {
		showError(e, "Could not stop the broadcast.");
	} finally {
		busy.value = "";
	}
}

function added(b) {
	rows.value = [b, ...rows.value];
}

onMounted(load);
</script>
