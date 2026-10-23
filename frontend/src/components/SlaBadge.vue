<template>
	<span
		v-if="chat.sla"
		class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium"
		:class="classes[chat.sla]"
		:title="title"
	>
		<LucideTimer class="size-3" />{{ text }}
	</span>
</template>

<script setup>
import { computed } from "vue";
import dayjs from "dayjs";
import LucideTimer from "~icons/lucide/timer";

const props = defineProps({ chat: { type: Object, required: true } });

const classes = {
	Breached: "bg-surface-red-2 text-ink-red-4",
	"Due Soon": "bg-surface-amber-2 text-ink-amber-4",
	"On Track": "bg-surface-gray-2 text-ink-gray-6",
	Met: "bg-surface-green-2 text-ink-green-4",
};

const text = computed(() => {
	const c = props.chat;
	if (c.sla === "Met") return "SLA met";
	if (!c.due) return c.sla;
	const mins = Math.round((dayjs(c.due) - dayjs()) / 60000);
	const span = (m) =>
		Math.abs(m) >= 60 ? `${Math.round(Math.abs(m) / 60)}h` : `${Math.abs(m)}m`;
	return mins < 0 ? `${span(mins)} late` : `due in ${span(mins)}`;
});

const title = computed(() =>
	props.chat.due
		? `${props.chat.sla} · due ${dayjs(props.chat.due).format("D MMM, h:mm A")}`
		: props.chat.sla
);
</script>
