<template>
	<div class="flex flex-wrap items-center justify-end gap-1.5">
		<SlaBadge v-if="!compact" :chat="chat" />

		<Button
			v-if="chat.is_group && !compact"
			size="sm"
			variant="ghost"
			tooltip="Group settings, members and invite link"
			@click="groupOpen = true"
		>
			<template #prefix><LucideSettings2 class="size-3.5" /></template>
			Group
		</Button>
		<GroupSettingsDialog
			v-if="chat.is_group"
			v-model="groupOpen"
			:chat="chat"
			@renamed="emit('changed')"
		/>

		<template v-if="!compact">
			<Dropdown :options="labelOptions">
				<Button size="sm" variant="ghost" tooltip="Label" aria-label="Label">
					<template #prefix><LucideTag class="size-3.5" /></template>
					{{ labelText }}
				</Button>
			</Dropdown>

			<Dropdown v-if="boot.is_manager" :options="teamOptions">
				<Button size="sm" variant="ghost" :label="chat.team || 'No team'" tooltip="Team">
					<template #prefix><LucideUsersRound class="size-3.5" /></template>
				</Button>
			</Dropdown>
			<Badge v-else-if="chat.team" :label="chat.team" variant="subtle" />

			<Dropdown :options="assignOptions" @update:open="(o) => o && loadPeople()">
				<Button
					size="sm"
					variant="ghost"
					tooltip="Assign (a: to me)"
					aria-label="Assign (a: to me)"
				>
					<template #prefix><LucideUserPlus class="size-3.5" /></template>
					{{ assigneeLabel }}
				</Button>
			</Dropdown>
		</template>

		<Button
			v-if="!compact"
			size="sm"
			variant="ghost"
			:class="chat.ai_off ? '!text-ink-amber-7' : ''"
			:tooltip="
				chat.ai_off
					? 'AI is off for this customer. Click to let the AI read and answer again.'
					: 'Keep this customer away from the AI (no tags, auto reply, summary or learning)'
			"
			:aria-label="chat.ai_off ? 'Turn the AI back on' : 'Keep away from the AI'"
			:loading="aiBusy"
			@click="toggleAi"
		>
			<template #prefix>
				<LucideBotOff v-if="chat.ai_off" class="size-3.5" />
				<LucideBot v-else class="size-3.5" />
			</template>
			{{ chat.ai_off ? "AI off" : "AI" }}
		</Button>

		<Dropdown :options="statusOptions">
			<Button
				class="tap"
				size="sm"
				:theme="statusTheme[chat.status]"
				variant="subtle"
				:class="chat.status === 'Pending' ? '!bg-surface-amber-2 !text-ink-amber-8' : ''"
				:loading="busy"
				tooltip="Status (e: resolve)"
				aria-label="Status (e: resolve)"
			>
				{{ chat.status }}
				<template #suffix><LucideChevronDown class="size-3.5" /></template>
			</Button>
		</Dropdown>

		<!-- a customer who asked for no broadcasts -->
		<Dropdown
			v-if="!compact && !chat.is_group"
			:options="[broadcastOption, ...privacyOptions]"
		>
			<Button
				size="sm"
				variant="ghost"
				:class="chat.no_broadcasts ? '!text-ink-amber-7' : ''"
				:tooltip="
					chat.no_broadcasts
						? 'This customer gets no broadcasts'
						: 'More: broadcasts to this customer'
				"
				aria-label="More actions"
			>
				<template #icon>
					<LucideMegaphoneOff v-if="chat.no_broadcasts" class="size-4" />
					<LucideEllipsisVertical v-else class="size-4" />
				</template>
			</Button>
		</Dropdown>

		<PrivacyDialog v-if="privacyOpen" v-model="privacyOpen" :number="chat.contact_number" />

		<!-- a phone: everything but the status, in one menu -->
		<Dropdown v-if="compact" :options="moreOptions" @update:open="(o) => o && loadPeople()">
			<Button class="tap" variant="ghost" aria-label="More actions">
				<template #icon><LucideEllipsisVertical class="size-4" /></template>
			</Button>
		</Dropdown>
	</div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, ref } from "vue";
import LucideSettings2 from "~icons/lucide/settings-2";
import { Badge, Button, Dropdown, call, toast } from "frappe-ui";
import LucideSquare from "~icons/lucide/square";
import LucideSquareCheck from "~icons/lucide/square-check";
import { showError } from "@/utils/errors";
import { ask } from "@/utils/confirm";
import LucideTag from "~icons/lucide/tag";
import LucideUsersRound from "~icons/lucide/users-round";
import LucideUserPlus from "~icons/lucide/user-plus";
import LucideChevronDown from "~icons/lucide/chevron-down";
import LucideEllipsisVertical from "~icons/lucide/ellipsis-vertical";
import LucideBot from "~icons/lucide/bot";
import LucideBotOff from "~icons/lucide/bot-off";
import LucideMegaphone from "~icons/lucide/megaphone";
import LucideMegaphoneOff from "~icons/lucide/megaphone-off";
import LucideShieldUser from "~icons/lucide/shield-user";
import SlaBadge from "@/components/SlaBadge.vue";
const PrivacyDialog = defineAsyncComponent(() => import("@/components/PrivacyDialog.vue"));
import { boot } from "@/boot";
import { inbox, loadTeams } from "@/stores/inbox";

const GroupSettingsDialog = defineAsyncComponent(() =>
	import("@/components/GroupSettingsDialog.vue")
);

const props = defineProps({
	chat: { type: Object, required: true },
	compact: { type: Boolean, default: false }, // a phone: the status, and the rest in a More menu
	more: { type: Array, default: () => [] }, // the parent's own entries for that menu
});
const groupOpen = ref(false); // GroupSettingsDialog
const emit = defineEmits(["changed"]);

const people = ref([]);
// Button has no amber theme; Pending gets its amber colours from a class instead.
const statusTheme = { Open: "green", Pending: "gray", Resolved: "blue", Closed: "gray" };
const busy = ref(false);
const ticked = (on) => (on ? LucideSquareCheck : LucideSquare);

const assigneeLabel = computed(() => {
	const a = props.chat.assigned_to || [];
	if (!a.length) return "Assign";
	const name = (u) => people.value.find((p) => p.name === u)?.full_name || u.split("@")[0];
	return a.length === 1 ? name(a[0]) : `${name(a[0])} +${a.length - 1}`;
});

const labelText = computed(() =>
	props.chat.labels?.length ? props.chat.labels.join(", ") : "Label"
);

// One change at a time; each one says what it did, and a failure says so.
async function act(method, args, done) {
	if (busy.value) return;
	busy.value = true;
	try {
		await call(`periskope_connector.api.chat_actions.${method}`, {
			chats: [props.chat.name],
			...args,
		});
		toast.success(done);
		emit("changed");
	} catch (e) {
		showError(e);
	} finally {
		busy.value = false;
	}
}

// Keep this customer away from the AI (asks first), or let the AI back.
const aiBusy = ref(false);
async function toggleAi() {
	const off = !props.chat.ai_off;
	if (
		off &&
		!(await ask(
			"Nothing of this customer's chats goes to the AI any more: no tags, auto reply, welcome, CRM summary, voice-note transcript or learning. You can turn it back on any time.",
			{ title: "Keep away from the AI?", confirmLabel: "Turn AI off" }
		))
	)
		return;
	aiBusy.value = true;
	try {
		await call("periskope_connector.api.chat_actions.set_ai_off", {
			chats: [props.chat.name],
			off: off ? 1 : 0,
		});
		toast.success(off ? "AI is off for this customer" : "AI is back on");
		emit("changed");
	} catch (e) {
		showError(e);
	} finally {
		aiBusy.value = false;
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
		selected: props.chat.team === t.name,
		onClick: () => act("set_team", { team: t.name }, `Moved to ${t.name}`),
	})),
	{ label: "No team", onClick: () => act("set_team", { team: "" }, "Team removed") },
]);

const labelOptions = computed(() =>
	boot.tags.map((t) => {
		const has = (props.chat.labels || []).includes(t);
		return {
			label: t,
			icon: ticked(has),
			onClick: () =>
				act(
					has ? "remove_label" : "add_label",
					{ tag: t },
					has ? `Removed ${t}` : `Labelled ${t}`
				),
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
			label: p.full_name || p.name,
			icon: ticked(on),
			onClick: () =>
				on
					? unassign(p.name)
					: act("assign", { users: [p.name] }, `Assigned to ${p.full_name || p.name}`),
		});
	}
	return out;
});

// The phone's More menu: the same choices as the buttons, each list as a submenu.
const moreOptions = computed(() => [
	...(props.chat.is_group
		? [
				{
					label: "Group settings",
					icon: LucideSettings2,
					onClick: () => (groupOpen.value = true),
				},
		  ]
		: []),
	...(labelOptions.value.length
		? [
				{
					label: props.chat.labels?.length ? `Label: ${labelText.value}` : "Label",
					icon: LucideTag,
					submenu: labelOptions.value,
				},
		  ]
		: []),
	...(boot.is_manager
		? [
				{
					label: `Team: ${props.chat.team || "none"}`,
					icon: LucideUsersRound,
					submenu: teamOptions.value,
				},
		  ]
		: []),
	{ label: assigneeLabel.value, icon: LucideUserPlus, submenu: assignOptions.value },
	{
		label: props.chat.ai_off ? "Turn the AI back on" : "Keep away from the AI",
		icon: props.chat.ai_off ? LucideBotOff : LucideBot,
		onClick: toggleAi,
	},
	...(props.chat.is_group ? [] : [broadcastOption.value, ...privacyOptions.value]),
	...props.more,
]);

// A privacy request: download or erase this customer's WhatsApp data (managers).
const privacyOpen = ref(false);
const privacyOptions = computed(() =>
	boot.is_manager && props.chat.contact_number
		? [
				{
					label: "This customer's data…",
					icon: LucideShieldUser,
					onClick: () => (privacyOpen.value = true),
				},
		  ]
		: []
);

// The customer asked for no broadcasts (also set when they write STOP), or agreed to them again.
const broadcastOption = computed(() => ({
	label: props.chat.no_broadcasts ? "Allow broadcasts again" : "No broadcasts to this customer",
	icon: props.chat.no_broadcasts ? LucideMegaphone : LucideMegaphoneOff,
	onClick: toggleBroadcasts,
}));

async function toggleBroadcasts() {
	const off = !props.chat.no_broadcasts;
	try {
		await call("periskope_connector.api.chat_actions.set_no_broadcasts", {
			chats: [props.chat.name],
			off: off ? 1 : 0,
		});
		toast.success(off ? "No more broadcasts to this customer" : "Broadcasts allowed again");
		emit("changed");
	} catch (e) {
		showError(e);
	}
}

async function unassign(user) {
	if (busy.value) return;
	busy.value = true;
	try {
		await call("periskope_connector.api.chat_actions.unassign", {
			chat: props.chat.name,
			user,
		});
		toast.success("Unassigned");
		emit("changed");
	} catch (e) {
		showError(e);
	} finally {
		busy.value = false;
	}
}

async function loadPeople() {
	try {
		people.value = await call("periskope_connector.api.chat_actions.assignable_users", {
			chat: props.chat.name,
		});
	} catch (e) {
		// the menu still offers "Assign to me"; the list loads again when it is opened
	}
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
