<template>
	<section
		class="overflow-hidden rounded-2xl border border-outline-gray-1 bg-surface-base shadow-sm"
	>
		<header class="flex items-center gap-2 border-b border-outline-gray-1 px-4 py-2.5">
			<span
				class="grid size-7 place-items-center rounded-lg bg-surface-violet-1 text-ink-violet-6"
			>
				<LucideSparkles class="size-4" />
			</span>
			<div class="min-w-0">
				<h2 class="text-base font-semibold text-ink-gray-9">Your brief</h2>
				<p class="text-xs text-ink-gray-5">{{ subtitle }}</p>
			</div>
			<Button
				class="ml-auto"
				variant="ghost"
				:loading="refreshing"
				:disabled="loading"
				tooltip="Write the brief again"
				aria-label="Write the brief again"
				@click="load(true)"
			>
				<template #icon><LucideRefreshCw class="size-4" /></template>
			</Button>
		</header>

		<div v-if="loading" class="flex flex-col gap-2.5 px-4 py-3">
			<div
				v-for="i in 3"
				:key="i"
				class="h-4 animate-pulse rounded bg-surface-gray-2"
				:style="{ width: `${90 - i * 15}%` }"
			/>
			<p class="text-xs text-ink-gray-5">Reading your chats, calls and tasks…</p>
		</div>
		<p v-else-if="error" class="px-4 py-3 text-sm text-ink-gray-6">
			{{ error }}
			<Button size="sm" class="ml-2" label="Try again" @click="load()" />
		</p>
		<ol v-else class="divide-y divide-outline-gray-1">
			<li
				v-for="(p, i) in brief?.points || []"
				:key="i"
				class="flex items-start gap-3 px-4 py-2.5"
			>
				<span
					class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-surface-gray-2 text-[11px] font-semibold text-ink-gray-6"
				>
					{{ i + 1 }}
				</span>
				<p class="min-w-0 flex-1 text-sm leading-relaxed text-ink-gray-8">{{ p.text }}</p>
				<Button
					v-if="p.chat || p.route"
					size="sm"
					variant="subtle"
					:label="p.chat ? 'Open' : 'Open in CRM'"
					@click="open(p)"
				/>
			</li>
		</ol>
	</section>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import dayjs from "dayjs";
import { Button, call, toast } from "frappe-ui";
import LucideSparkles from "~icons/lucide/sparkles";
import LucideRefreshCw from "~icons/lucide/refresh-cw";
import { errorText } from "@/utils/errors";

// Today's brief (api/brief.py): a few points on what matters today, written once a day and kept.
// The refresh button writes it again (the server allows that every few minutes).
const router = useRouter();
const brief = ref(null);
const loading = ref(true);
const refreshing = ref(false);
const error = ref("");

const subtitle = computed(() => {
	if (!brief.value) return "What matters today";
	const at = dayjs(brief.value.generated_at).format("h:mm A");
	return brief.value.by_ai ? `Written by the AI at ${at}` : `Made at ${at}`;
});

async function load(refresh = false) {
	refreshing.value = refresh;
	loading.value = !refresh;
	error.value = "";
	try {
		const before = brief.value?.generated_at;
		brief.value = await call("periskope_connector.api.brief.get_brief", {
			refresh: refresh ? 1 : 0,
		});
		if (refresh && brief.value.generated_at === before)
			toast.info("The brief was written a few minutes ago. Try again a little later.");
	} catch (e) {
		error.value = errorText(e, "The brief could not be written.");
	} finally {
		loading.value = refreshing.value = false;
	}
}

function open(p) {
	if (p.chat) router.push({ name: "Inbox", params: { chat: p.chat } });
	else if (p.route) window.location.href = p.route;
}

onMounted(() => load());
</script>
