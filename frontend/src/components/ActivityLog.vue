<template>
	<div
		class="flex h-full flex-col overflow-y-auto border-l border-outline-gray-1 bg-surface-base p-4"
	>
		<div v-if="!rows.length" class="text-sm text-ink-gray-5">
			Nothing has happened to this chat yet.
		</div>
		<div
			v-for="a in rows"
			:key="a.name"
			class="flex gap-2 border-b border-outline-gray-1 py-2 last:border-0"
		>
			<component
				:is="icons[a.action] || LucideDot"
				class="mt-0.5 size-4 shrink-0 text-ink-gray-5"
			/>
			<div class="min-w-0 flex-1">
				<div class="text-sm text-ink-gray-8">
					<span class="font-medium">{{ a.action }}</span>
					<span v-if="a.details"> · {{ a.details }}</span>
				</div>
				<div class="text-xs text-ink-gray-5">
					{{ a.owner_name }} · {{ fromNow(a.creation) }}
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { onMounted, ref, watch } from "vue";
import { call } from "frappe-ui";
import LucideUserPlus from "~icons/lucide/user-plus";
import LucideUserMinus from "~icons/lucide/user-minus";
import LucideCircleDot from "~icons/lucide/circle-dot";
import LucideRotateCcw from "~icons/lucide/rotate-ccw";
import LucideUsersRound from "~icons/lucide/users-round";
import LucideTag from "~icons/lucide/tag";
import LucideLink from "~icons/lucide/link";
import LucideTimerOff from "~icons/lucide/timer-off";
import LucideDot from "~icons/lucide/dot";
import { inbox } from "@/stores/inbox";
import { fromNow } from "@/utils/format";

const props = defineProps({ chat: { type: String, required: true } });
const rows = ref([]);
const icons = {
	Assigned: LucideUserPlus,
	Unassigned: LucideUserMinus,
	Status: LucideCircleDot,
	Reopened: LucideRotateCcw,
	Team: LucideUsersRound,
	Label: LucideTag,
	Linked: LucideLink,
	"SLA Breached": LucideTimerOff,
};

async function load() {
	rows.value = await call("periskope_connector.api.chat_actions.activity", { chat: props.chat });
}

watch(() => props.chat, load);
watch(
	() => inbox.version,
	() => inbox.changed.includes(props.chat) && load()
);
onMounted(load);
defineExpose({ reload: load });
</script>
