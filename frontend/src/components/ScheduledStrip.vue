<template>
	<div v-if="rows.length" class="flex justify-center px-4 pb-1 pt-2">
		<button
			class="flex items-center gap-1.5 rounded-full border border-outline-blue-1 bg-surface-blue-1 px-3 py-1 text-xs font-medium text-ink-blue-7 hover:bg-surface-blue-2"
			@click="open = true"
		>
			<LucideClock class="size-3.5" />
			{{ rows.length }} scheduled · next {{ when(rows[0].next_run) }}
		</button>
	</div>
	<Dialog v-model="open" :options="{ title: 'Scheduled in this chat', size: 'lg' }">
		<template #body-content>
			<div class="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
				<ScheduledItem
					v-for="r in rows"
					:key="r.name"
					:row="r"
					@edit="edit"
					@changed="replace"
					@deleted="drop"
				/>
				<p v-if="!rows.length" class="text-sm text-ink-gray-5">Nothing is scheduled.</p>
			</div>
		</template>
	</Dialog>
	<ScheduleDialog
		v-model="editOpen"
		:chat="chat"
		:editing="editing"
		:can-repeat="canRepeat"
		@saved="replace"
	/>
</template>

<script setup>
import { ref, watch } from "vue";
import { Dialog, call } from "frappe-ui";
import LucideClock from "~icons/lucide/clock";
import ScheduledItem from "@/components/ScheduledItem.vue";
import ScheduleDialog from "@/components/ScheduleDialog.vue";
import { when } from "@/utils/schedule";

// "2 scheduled · next Tomorrow, 10:00 AM" above the reply box; opens the chat's scheduled messages.
const props = defineProps({ chat: { type: String, required: true } });

const rows = ref([]);
const canRepeat = ref(false);
const open = ref(false);
const editOpen = ref(false);
const editing = ref(null);

async function load() {
	try {
		const r = await call("periskope_connector.api.scheduled.for_chat", { chat: props.chat });
		rows.value = r.scheduled;
		canRepeat.value = r.can_repeat;
	} catch {
		rows.value = []; // the chat still works without its schedules
	}
}

function edit(row) {
	editing.value = row;
	editOpen.value = true;
}
function replace(row) {
	if (!row) return load();
	const list = rows.value.filter((r) => r.name !== row.name);
	if (row.status !== "Completed") list.push(row);
	rows.value = list.sort((a, b) => (a.next_run < b.next_run ? -1 : 1));
}
function drop(row) {
	rows.value = rows.value.filter((r) => r.name !== row.name);
	if (!rows.value.length) open.value = false;
}

watch(() => props.chat, load, { immediate: true });
defineExpose({ load, add: replace });
</script>
