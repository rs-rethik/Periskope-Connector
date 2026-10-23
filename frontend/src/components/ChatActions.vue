<template>
	<div class="flex flex-wrap items-center justify-end gap-1.5">
		<SlaBadge :chat="chat" />

		<Dropdown :options="labelOptions">
			<Button size="sm" variant="ghost" title="Labels">
				<template #prefix><LucideTag class="size-3.5" /></template>
				{{ chat.labels?.length ? chat.labels.join(", ") : "Label" }}
			</Button>
		</Dropdown>

		<Dropdown v-if="boot.is_manager" :options="teamOptions">
			<Button size="sm" variant="ghost" :label="chat.team || 'No team'" title="Team">
				<template #prefix><LucideUsersRound class="size-3.5" /></template>
			</Button>
		</Dropdown>
		<Badge v-else-if="chat.team" :label="chat.team" variant="subtle" />

		<Dropdown :options="assignOptions" @update:open="(o) => o && loadPeople()">
			<Button size="sm" variant="ghost" title="Assign (a: to me)">
				<template #prefix><LucideUserPlus class="size-3.5" /></template>
				{{ assigneeLabel }}
			</Button>
		</Dropdown>

		<Dropdown :options="statusOptions">
			<Button
				size="sm"
				:theme="statusTheme[chat.status]"
				variant="subtle"
				title="Status (e: resolve)"
			>
				{{ chat.status }}
				<template #suffix><LucideChevronDown class="size-3.5" /></template>
			</Button>
		</Dropdown>
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { Badge, Button, Dropdown, call, toast } from "frappe-ui";
import LucideTag from "~icons/lucide/tag";
import LucideUsersRound from "~icons/lucide/users-round";
import LucideUserPlus from "~icons/lucide/user-plus";
import LucideChevronDown from "~icons/lucide/chevron-down";
import SlaBadge from "@/components/SlaBadge.vue";
import { boot } from "@/boot";
import { inbox, loadTeams } from "@/stores/inbox";

const props = defineProps({ chat: { type: Object, required: true } });
const emit = defineEmits(["changed"]);

const people = ref([]);
const statusTheme = { Open: "green", Pending: "orange", Resolved: "blue", Closed: "gray" };

const assigneeLabel = computed(() => {
	const a = props.chat.assigned_to || [];
	if (!a.length) return "Assign";
	const name = (u) => people.value.find((p) => p.name === u)?.full_name || u.split("@")[0];
	return a.length === 1 ? name(a[0]) : `${name(a[0])} +${a.length - 1}`;
});

async function act(method, args, done) {
	try {
		await call(`periskope_connector.api.chat_actions.${method}`, {
			chats: [props.chat.name],
			...args,
		});
		if (done) toast.success(done);
		emit("changed");
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "That did not work.");
	}
}

const statusOptions = computed(() =>
	["Open", "Pending", "Resolved", "Closed"].map((s) => ({
		label: s,
		onClick: () => act("set_status", { status: s }, `Marked ${s}`),
	}))
);

const teamOptions = computed(() => [
	...inbox.teams.map((t) => ({
		label: t.name,
		onClick: () => act("set_team", { team: t.name }),
	})),
	{ label: "No team", onClick: () => act("set_team", { team: "" }) },
]);

const labelOptions = computed(() =>
	boot.tags.map((t) => {
		const has = (props.chat.labels || []).includes(t);
		return {
			label: (has ? "✓ " : "   ") + t,
			onClick: () => act(has ? "remove_label" : "add_label", { tag: t }),
		};
	})
);

const assignOptions = computed(() => {
	const mine = (props.chat.assigned_to || []).includes(boot.user);
	const out = [];
	if (!mine)
		out.push({
			label: "Assign to me",
			onClick: () => act("assign_to_me", {}, "Assigned to you"),
		});
	for (const p of people.value) {
		const on = (props.chat.assigned_to || []).includes(p.name);
		if (p.name === boot.user && !on) continue;
		out.push({
			label: (on ? "✓ " : "   ") + (p.full_name || p.name),
			onClick: () =>
				on
					? unassign(p.name)
					: act("assign", { users: [p.name] }, `Assigned to ${p.full_name || p.name}`),
		});
	}
	return out;
});

async function unassign(user) {
	try {
		await call("periskope_connector.api.chat_actions.unassign", {
			chat: props.chat.name,
			user,
		});
		emit("changed");
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "That did not work.");
	}
}

async function loadPeople() {
	people.value = await call("periskope_connector.api.chat_actions.assignable_users", {
		chat: props.chat.name,
	});
}

onMounted(() => {
	loadPeople();
	if (boot.is_manager && !inbox.teams.length) loadTeams();
});

defineExpose({
	assignToMe: () => act("assign_to_me", {}, "Assigned to you"),
	resolve: () => act("set_status", { status: "Resolved" }, "Marked Resolved"),
});
</script>
