<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="page-head">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Health</h1>
			<span class="text-sm text-ink-gray-5"
				>Live figures of the connection, the AI and the agent.</span
			>
			<Button class="ml-auto" label="Refresh" :loading="loading" @click="load" />
			<Button
				v-if="boot.is_admin"
				label="Fetch missed messages (7 days)"
				:loading="fetching"
				@click="backfill"
			/>
			<Button label="Flow diagram" @click="$router.push({ name: 'Flow' })" />
		</div>
		<div v-if="h" class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
			<div
				v-if="h.test_mode_on"
				class="mb-4 rounded-lg bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-8"
			>
				Test mode is {{ h.test_mode }}
			</div>
			<div
				v-for="w in [h.capture_problem, ...(h.warnings || [])].filter(Boolean)"
				:key="w"
				class="mb-4 flex items-start gap-2 rounded-lg bg-surface-red-1 px-3 py-2 text-sm text-ink-red-8"
			>
				<LucideCircleAlert class="mt-0.5 size-4 shrink-0" />
				{{ w }}
			</div>
			<div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
				<section
					v-for="g in groups"
					:key="g.title"
					class="rounded-lg border border-outline-gray-1 bg-surface-base p-4"
				>
					<h2 class="mb-2 text-sm font-medium text-ink-gray-9">{{ g.title }}</h2>
					<dl class="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-sm">
						<template v-for="row in g.rows" :key="row[0]">
							<dt class="text-ink-gray-5">{{ row[0] }}</dt>
							<dd class="text-right text-ink-gray-8">{{ row[1] ?? "—" }}</dd>
						</template>
					</dl>
				</section>
			</div>
			<section
				v-if="failedRows.length"
				class="mt-4 rounded-lg border border-outline-gray-1 bg-surface-base p-4"
			>
				<h2 class="mb-1 text-sm font-medium text-ink-gray-9">
					Messages the AI could not read ({{ failedRows.length }})
				</h2>
				<p class="mb-2 text-xs text-ink-gray-5">
					Their escalation, lead or tasks have not happened. Retry once the cause is
					fixed.
				</p>
				<ul class="divide-y divide-outline-gray-1">
					<li v-for="m in failedRows" :key="m.name" class="flex items-start gap-3 py-2">
						<button
							class="min-w-0 flex-1 text-left"
							@click="
								m.periskope_chat &&
									$router.push({
										name: 'Inbox',
										params: { chat: m.periskope_chat },
									})
							"
						>
							<div class="truncate text-sm text-ink-gray-8">
								{{
									m.chat_name || (m.contact_number ? "+" + m.contact_number : "")
								}}
								·
								{{ m.body || "[" + m.message_type + "]" }}
							</div>
							<div class="truncate text-xs text-ink-red-7">
								{{ when(m.message_timestamp) }} · {{ m.classification_error }}
							</div>
						</button>
						<Button
							size="sm"
							label="Retry"
							:loading="retrying === m.name"
							@click="retry(m)"
						/>
					</li>
				</ul>
			</section>
			<section
				v-if="h.has_problem"
				class="mt-4 rounded-lg border border-outline-red-2 bg-surface-red-1 p-4"
			>
				<h2 class="mb-1 flex items-center gap-1.5 text-sm font-medium text-ink-red-8">
					<LucideCircleAlert class="size-4" /> Last problem
				</h2>
				<pre class="whitespace-pre-wrap text-xs text-ink-gray-8">{{ h.last_problem }}</pre>
			</section>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { Button, call, toast } from "frappe-ui";
import LucideCircleAlert from "~icons/lucide/circle-alert";
import { boot } from "@/boot";
import { showError } from "@/utils/errors";
import { dayLabel, fromNow, messageTime } from "@/utils/format";

const h = ref(null);
const loading = ref(false);
const fetching = ref(false);

// "Today, 6:35 PM (2 hours ago)" for a time the server sends as "2026-10-09 18:35:21.169532".
const when = (value) =>
	value ? `${dayLabel(value)}, ${messageTime(value)} (${fromNow(value)})` : value;

const groups = computed(() =>
	h.value
		? [
				{
					title: "Messages in",
					rows: [
						["Last webhook", when(h.value.last_webhook)],
						["Webhook logs not processed", h.value.unprocessed],
						["Rejected webhook calls (24 h)", h.value.rejected_webhooks],
						["Messages per number today", h.value.phones],
					],
				},
				{
					title: "AI",
					rows: [
						["Last AI run", when(h.value.last_ai_run)],
						["Waiting to be tagged", h.value.pending],
						["Failed", h.value.failed],
						["Escalations held (hourly limit)", h.value.held_escalations],
						["Tokens this month", h.value.month_tokens],
						["Budget", h.value.budget],
					],
				},
				{
					title: "Agent",
					rows: [
						["Learning agent", h.value.agent],
						["Auto reply", h.value.auto_reply],
						["CRM updates", h.value.crm],
					],
				},
		  ]
		: []
);

const failedRows = ref([]);
const retrying = ref("");

async function load() {
	loading.value = true;
	try {
		h.value = await call("periskope_connector.api.settings.get_health");
	} finally {
		loading.value = false;
	}
	// the list is extra: the figures show even if it cannot be read
	call("periskope_connector.api.settings.failed_messages")
		.then((rows) => (failedRows.value = rows || []))
		.catch(() => (failedRows.value = []));
}

async function retry(m) {
	retrying.value = m.name;
	try {
		await call("periskope_connector.api.settings.retry_failed_message", { message: m.name });
		failedRows.value = failedRows.value.filter((x) => x.name !== m.name);
		toast.success("Sent to the AI again");
	} catch (e) {
		showError(e, "Could not retry.");
	} finally {
		retrying.value = "";
	}
}

async function backfill() {
	fetching.value = true;
	try {
		const r = await call("periskope_connector.api.settings.backfill_messages", { days: 7 });
		toast.success(
			`Checked ${r?.chats ?? 0} chat(s): ${r?.recovered ?? 0} missed message(s) saved`
		);
		load();
	} catch (e) {
		showError(e, "Could not fetch.");
	} finally {
		fetching.value = false;
	}
}

onMounted(load);
</script>
