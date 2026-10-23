<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="flex flex-wrap items-center gap-2 border-b border-outline-gray-1 px-5 py-3">
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
				v-if="h.test_mode"
				class="mb-4 rounded-lg bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-4"
			>
				{{ h.test_mode }}
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
				v-if="h.last_problem"
				class="mt-4 rounded-lg border border-outline-red-2 bg-surface-red-1 p-4"
			>
				<h2 class="mb-1 flex items-center gap-1.5 text-sm font-medium text-ink-red-4">
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

const h = ref(null);
const loading = ref(false);
const fetching = ref(false);

const groups = computed(() =>
	h.value
		? [
				{
					title: "Messages in",
					rows: [
						["Last webhook", h.value.last_webhook],
						["Webhook logs not processed", h.value.unprocessed],
						["Messages per number today", h.value.phones],
					],
				},
				{
					title: "AI",
					rows: [
						["Last AI run", h.value.last_ai_run],
						["Waiting to be tagged", h.value.pending],
						["Failed", h.value.failed],
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

async function load() {
	loading.value = true;
	try {
		h.value = await call("periskope_connector.api.settings.get_health");
	} finally {
		loading.value = false;
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
		toast.error(e?.messages?.[0] || e?.message || "Could not fetch.");
	} finally {
		fetching.value = false;
	}
}

onMounted(load);
</script>
