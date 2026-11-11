<template>
	<div class="flex flex-col gap-2 rounded-xl border border-outline-gray-2 bg-surface-base p-3">
		<div class="flex items-start gap-2">
			<span
				class="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full"
				:class="
					row.status === 'Paused'
						? 'bg-surface-amber-2 text-ink-amber-8'
						: 'bg-surface-blue-2 text-ink-blue-7'
				"
			>
				<LucideRepeat v-if="row.is_repeat" class="size-3.5" />
				<LucideClock v-else class="size-3.5" />
			</span>
			<div class="min-w-0 flex-1">
				<div v-if="showChat" class="truncate text-sm font-semibold text-ink-gray-9">
					{{ row.chat_title }}
				</div>
				<div class="text-sm font-medium text-ink-gray-8">
					{{ row.status === "Completed" ? "Finished" : when(row.next_run) }}
					<span class="font-normal text-ink-gray-5">· {{ repeatText(row) }}</span>
				</div>
				<div class="text-xs text-ink-gray-5">
					<template v-if="row.scheduled_by_name"
						>by {{ row.scheduled_by_name }}</template
					>
					<template v-if="row.skip_next"> · the next run is skipped</template>
				</div>
			</div>
			<span
				v-if="row.status !== 'Scheduled'"
				class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
				:class="
					row.status === 'Paused'
						? 'bg-surface-amber-2 text-ink-amber-8'
						: 'bg-surface-gray-2 text-ink-gray-6'
				"
			>
				{{
					row.paused_for_test_mode
						? "Paused · test mode"
						: row.status === "Paused"
						? "Paused"
						: "Finished"
				}}
			</span>
		</div>
		<p class="whitespace-pre-wrap break-words text-sm text-ink-gray-8">
			<LucidePaperclip v-if="row.file" class="mr-1 inline size-3.5 text-ink-gray-5" />{{
				row.message || row.file?.split("/").pop()
			}}
		</p>
		<p v-if="row.last_failure" class="text-xs text-ink-red-7">
			Last run not sent: {{ row.last_failure }}
		</p>

		<div class="flex flex-wrap items-center gap-1.5">
			<template v-if="row.can_edit && row.status !== 'Completed'">
				<Button size="sm" label="Edit" @click="$emit('edit', row)">
					<template #prefix><LucidePencil class="size-3.5" /></template>
				</Button>
				<Button
					v-if="!(row.paused_for_test_mode && boot.test_mode)"
					size="sm"
					:label="row.status === 'Paused' ? 'Resume' : 'Pause'"
					:loading="busy === 'pause'"
					@click="act('pause', { paused: row.status === 'Paused' ? 0 : 1 })"
				/>
				<Button
					v-if="row.is_repeat && row.status === 'Scheduled' && !row.skip_next"
					size="sm"
					label="Skip next"
					:loading="busy === 'skip'"
					@click="act('skip', { skip_next: 1 })"
				/>
				<Button
					size="sm"
					variant="ghost"
					theme="red"
					label="Delete"
					:loading="busy === 'delete'"
					@click="remove"
				/>
			</template>
			<Button
				class="ml-auto"
				size="sm"
				variant="ghost"
				:label="runs ? 'Hide runs' : 'Runs'"
				:loading="busy === 'runs'"
				@click="toggleRuns"
			/>
		</div>
		<ErrorMessage :message="error" />
		<div v-if="runs" class="rounded-lg bg-surface-gray-1 p-2 text-xs text-ink-gray-8">
			<div v-if="!runs.length" class="text-ink-gray-5">No runs yet.</div>
			<div v-for="(r, i) in runs" :key="i" class="flex justify-between gap-2 py-0.5">
				<span class="text-ink-gray-7">{{ when(r.at) }}</span>
				<span
					:class="
						r.ok === false
							? 'text-ink-red-7'
							: r.ok
							? 'text-ink-green-7'
							: 'text-ink-gray-5'
					"
					>{{ r.result }}</span
				>
			</div>
		</div>
	</div>
</template>

<script setup>
import { ref } from "vue";
import { Button, ErrorMessage, call } from "frappe-ui";
import LucideClock from "~icons/lucide/clock";
import LucideRepeat from "~icons/lucide/repeat";
import LucidePencil from "~icons/lucide/pencil";
import LucidePaperclip from "~icons/lucide/paperclip";
import { ask } from "@/utils/confirm";
import { boot } from "@/boot";
import { errorText } from "@/utils/errors";
import { repeatText, when } from "@/utils/schedule";

// One scheduled message (api/scheduled.py): when, how often, by whom, and its actions.
const props = defineProps({
	row: { type: Object, required: true },
	showChat: { type: Boolean, default: false },
});
const emit = defineEmits(["edit", "changed", "deleted"]);

const API = "periskope_connector.api.scheduled.";
const busy = ref("");
const error = ref("");
const runs = ref(null);

async function act(kind, args) {
	busy.value = kind;
	error.value = "";
	try {
		emit("changed", await call(API + "update", { name: props.row.name, ...args }));
	} catch (e) {
		error.value = errorText(e, "That did not work.");
	} finally {
		busy.value = "";
	}
}

async function remove() {
	const ok = await ask("It will not be sent. Its past runs stay in Periskope.", {
		title: "Delete this scheduled message?",
		confirmLabel: "Delete",
		danger: true,
	});
	if (!ok) return;
	busy.value = "delete";
	try {
		await call(API + "delete", { name: props.row.name });
		emit("deleted", props.row);
	} catch (e) {
		error.value = errorText(e, "It could not be deleted.");
	} finally {
		busy.value = "";
	}
}

async function toggleRuns() {
	if (runs.value) return (runs.value = null);
	busy.value = "runs";
	try {
		runs.value = await call(API + "runs", { name: props.row.name });
	} catch (e) {
		error.value = errorText(e, "The runs could not be loaded.");
	} finally {
		busy.value = "";
	}
}
</script>
