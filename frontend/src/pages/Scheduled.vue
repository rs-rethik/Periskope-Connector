<template>
	<div class="flex h-full min-h-0 flex-col">
		<div class="page-head">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Scheduled messages</h1>
			<span class="text-sm text-ink-gray-5">
				Sent by Periskope at their time, on the Periskope numbers.
			</span>
			<div class="ml-auto flex flex-wrap items-center gap-2">
				<div class="w-56">
					<FormControl
						v-model="filters.status"
						type="select"
						:options="[
							{ label: 'Scheduled and paused', value: '' },
							{ label: 'Scheduled', value: 'Scheduled' },
							{ label: 'Paused', value: 'Paused' },
							{ label: 'Finished', value: 'Completed' },
						]"
					/>
				</div>
				<div class="w-56">
					<FormControl
						v-model="filters.repeat"
						type="select"
						:options="[
							{ label: 'One-time and repeating', value: '' },
							{ label: 'One-time', value: '0' },
							{ label: 'Repeating', value: '1' },
						]"
					/>
				</div>
				<div class="w-48">
					<FormControl v-model="filters.number" type="select" :options="numberOptions" />
				</div>
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
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto p-5">
			<div v-if="shown.length" class="grid gap-3 lg:grid-cols-2">
				<div v-for="r in shown" :key="r.name" class="flex flex-col gap-1">
					<ScheduledItem
						:row="r"
						show-chat
						@edit="edit"
						@changed="load"
						@deleted="load"
					/>
					<button
						v-if="r.periskope_chat"
						class="self-end text-xs text-ink-gray-5 hover:text-ink-gray-8"
						@click="router.push({ name: 'Inbox', params: { chat: r.periskope_chat } })"
					>
						Open the chat →
					</button>
				</div>
			</div>
			<p v-else-if="!loading" class="py-16 text-center text-sm text-ink-gray-5">
				Nothing is scheduled. Use the clock beside Send in a chat to schedule a message.
			</p>
		</div>
		<ScheduleDialog
			v-if="editing"
			v-model="editOpen"
			:chat="editing.periskope_chat"
			:editing="editing"
			can-repeat
			@saved="load"
		/>
	</div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Button, FormControl, call } from "frappe-ui";
import LucideRefreshCw from "~icons/lucide/refresh-cw";
import ScheduledItem from "@/components/ScheduledItem.vue";
import ScheduleDialog from "@/components/ScheduleDialog.vue";
import { boot } from "@/boot";
import { showError } from "@/utils/errors";

// Every scheduled message (managers): api/scheduled.all_scheduled.
const router = useRouter();
const rows = ref([]);
const loading = ref(false);
const filters = ref({ status: "", repeat: "", number: "" });
const editing = ref(null);
const editOpen = ref(false);

const numberOptions = computed(() => [
	{ label: "All numbers", value: "" },
	...boot.numbers
		.filter((n) => n.source !== "Meta")
		.map((n) => ({ label: n.label, value: n.name })),
]);
// "Scheduled and paused" leaves the finished ones out
const shown = computed(() =>
	filters.value.status ? rows.value : rows.value.filter((r) => r.status !== "Completed")
);

async function load() {
	loading.value = true;
	try {
		rows.value = await call("periskope_connector.api.scheduled.all_scheduled", {
			status: filters.value.status || null,
			repeat: filters.value.repeat || null,
			number: filters.value.number || null,
		});
	} catch (e) {
		showError(e, "Could not load the scheduled messages.");
	} finally {
		loading.value = false;
	}
}

function edit(row) {
	editing.value = row;
	editOpen.value = true;
}

watch(filters, load, { deep: true });
onMounted(load);
</script>
