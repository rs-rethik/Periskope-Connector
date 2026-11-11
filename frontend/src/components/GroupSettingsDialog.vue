<template>
	<Dialog v-model="open" :options="{ title: 'Group settings', size: 'xl' }">
		<template #body-content>
			<div v-if="loading" class="flex justify-center py-10">
				<LoadingIndicator class="size-5" />
			</div>
			<p v-else-if="loadError" class="py-6 text-center text-sm text-ink-gray-6">
				{{ loadError }}
			</p>
			<div v-else-if="g" class="flex flex-col gap-4">
				<!-- who we are in the group -->
				<div class="flex items-center gap-3">
					<img
						v-if="g.image"
						:src="g.image"
						class="size-12 rounded-full object-cover"
						alt=""
					/>
					<span
						v-else
						class="grid size-12 place-items-center rounded-full bg-surface-gray-2 text-ink-gray-6"
					>
						<LucideUsersRound class="size-6" />
					</span>
					<div class="min-w-0">
						<div class="truncate text-base font-semibold text-ink-gray-9">
							{{ g.name }}
						</div>
						<div class="text-sm text-ink-gray-5">
							{{ g.member_count }} members ·
							{{
								g.we_are_admin
									? "our number is an admin"
									: "our number is not an admin"
							}}
						</div>
					</div>
				</div>
				<p
					v-if="g.blocked || !canEdit"
					class="rounded-lg bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-8"
				>
					{{
						g.blocked ||
						(boot.is_manager
							? "Our number is not an admin of this group, so WhatsApp lets it change little here."
							: "Only managers can change a group's settings.")
					}}
				</p>

				<div class="flex gap-1 rounded-lg bg-surface-gray-2 p-0.5 text-sm">
					<button
						v-for="t in tabs"
						:key="t.key"
						class="flex-1 rounded-md px-3 py-1.5 font-medium"
						:class="
							tab === t.key
								? 'bg-surface-base text-ink-gray-9 shadow-sm'
								: 'text-ink-gray-6'
						"
						@click="tab = t.key"
					>
						{{ t.label }}
					</button>
				</div>

				<!-- settings -->
				<div v-if="tab === 'settings'" class="flex flex-col gap-3">
					<FormControl
						v-model="form.name"
						label="Group name"
						:disabled="!g.can_change"
					/>
					<FormControl
						v-model="form.description"
						type="textarea"
						label="Description"
						:rows="3"
						:disabled="!g.can_change"
					/>
					<div class="flex flex-col gap-2 rounded-xl border border-outline-gray-2 p-3">
						<label
							v-for="s in switches"
							:key="s.key"
							class="flex items-start justify-between gap-3 text-sm text-ink-gray-8"
						>
							<span>
								<span class="font-medium text-ink-gray-8">{{ s.label }}</span>
								<span class="block text-xs text-ink-gray-5">{{ s.help }}</span>
							</span>
							<Switch v-model="form[s.key]" :disabled="!g.can_change" />
						</label>
						<div
							class="flex items-center justify-between gap-3 text-sm text-ink-gray-8"
						>
							<span>
								<span class="font-medium text-ink-gray-8"
									>Disappearing messages</span
								>
								<span class="block text-xs text-ink-gray-5"
									>New messages vanish after this time.</span
								>
							</span>
							<div class="w-36">
								<FormControl
									v-model="form.ephemeral"
									type="select"
									:disabled="!g.can_change"
									:options="[
										{ label: 'Unchanged', value: '' },
										{ label: 'Off', value: '0' },
										{ label: '24 hours', value: '86400' },
										{ label: '7 days', value: '604800' },
										{ label: '90 days', value: '7776000' },
									]"
								/>
							</div>
						</div>
					</div>
				</div>

				<!-- members -->
				<div v-else-if="tab === 'members'" class="flex flex-col gap-3">
					<div v-if="g.can_change" class="flex gap-2">
						<FormControl
							v-model="toAdd"
							class="flex-1"
							placeholder="Numbers to add, with country code (comma separated)"
						/>
						<Button
							label="Add"
							:loading="busy === 'add'"
							:disabled="!toAdd.trim()"
							@click="act('add', toAdd)"
						/>
					</div>
					<FormControl v-model="search" placeholder="Search members" />
					<div
						class="max-h-72 divide-y divide-outline-gray-1 overflow-y-auto rounded-lg border border-outline-gray-2"
					>
						<div
							v-for="m in shownMembers"
							:key="m.number"
							class="flex items-center gap-2 px-3 py-2 text-sm text-ink-gray-8"
						>
							<div class="min-w-0 flex-1">
								<div class="truncate font-medium text-ink-gray-8">
									{{ m.name || `+${m.number}` }}
								</div>
								<div v-if="m.name" class="text-xs text-ink-gray-5">
									+{{ m.number }}
								</div>
							</div>
							<Badge
								v-if="m.is_super_admin"
								label="Owner"
								theme="blue"
								variant="subtle"
							/>
							<Badge
								v-else-if="m.is_admin"
								label="Admin"
								theme="green"
								variant="subtle"
							/>
							<Badge
								v-if="m.number === ourNumber"
								label="Our number"
								variant="subtle"
							/>
							<Dropdown
								v-if="g.can_change && m.number !== ourNumber && !m.is_super_admin"
								:options="memberOptions(m)"
							>
								<Button size="sm" variant="ghost" aria-label="Member actions">
									<template #icon><LucideEllipsis class="size-4" /></template>
								</Button>
							</Dropdown>
						</div>
					</div>
				</div>

				<!-- invite link -->
				<div v-else class="flex flex-col gap-3 text-sm text-ink-gray-8">
					<template v-if="g.invite_link">
						<p class="text-ink-gray-6">
							Anyone with this link can ask to join the group.
						</p>
						<div class="flex gap-2">
							<FormControl :model-value="g.invite_link" class="flex-1" readonly />
							<Button label="Copy" @click="copy" />
						</div>
						<Button
							v-if="g.can_change"
							class="self-start"
							theme="red"
							variant="subtle"
							label="Reset the link"
							:loading="busy === 'invite'"
							@click="resetInvite"
						/>
					</template>
					<p v-else class="text-ink-gray-6">
						WhatsApp shows the invite link only to group admins, and our number is not
						one.
					</p>
				</div>

				<div
					v-if="results.length"
					class="rounded-lg bg-surface-gray-1 p-2 text-xs text-ink-gray-8"
				>
					<div
						v-for="r in results"
						:key="r.number"
						class="flex justify-between gap-2 py-0.5"
					>
						<span>+{{ r.number }}</span>
						<span :class="r.ok ? 'text-ink-green-7' : 'text-ink-red-7'">{{
							r.invited
								? "invite sent (WhatsApp would not add directly)"
								: r.message || (r.ok ? "done" : "failed")
						}}</span>
					</div>
				</div>
				<ErrorMessage :message="error" />
				<div v-if="g.can_change" class="border-t border-outline-gray-1 pt-3">
					<Button
						theme="red"
						variant="ghost"
						label="Our number leaves the group"
						:loading="busy === 'leave'"
						@click="leave"
					>
						<template #prefix><LucideLogOut class="size-4" /></template>
					</Button>
				</div>
			</div>
		</template>
		<template #actions>
			<Button
				v-if="g?.can_change && tab === 'settings'"
				class="w-full"
				variant="solid"
				label="Save settings"
				:loading="busy === 'save'"
				:disabled="!changed"
				@click="save"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import {
	Badge,
	Button,
	Dialog,
	Dropdown,
	ErrorMessage,
	FormControl,
	LoadingIndicator,
	Switch,
	call,
	toast,
} from "frappe-ui";
import LucideUsersRound from "~icons/lucide/users-round";
import LucideEllipsis from "~icons/lucide/ellipsis";
import LucideLogOut from "~icons/lucide/log-out";
import { boot } from "@/boot";
import { ask } from "@/utils/confirm";
import { errorText } from "@/utils/errors";

// A WhatsApp group's settings, members and invite link (api/group_settings.py). Managers change them;
// in test mode only a group of test numbers.
const open = defineModel({ type: Boolean, default: false });
const props = defineProps({ chat: { type: Object, required: true } });
const emit = defineEmits(["renamed"]);

const API = "periskope_connector.api.group_settings.";
const tabs = [
	{ key: "settings", label: "Settings" },
	{ key: "members", label: "Members" },
	{ key: "invite", label: "Invite link" },
];
const switches = [
	{
		key: "messages_admins_only",
		label: "Only admins send messages",
		help: "Members can read but not write.",
	},
	{
		key: "info_admins_only",
		label: "Only admins edit group info",
		help: "Name, description and picture.",
	},
	{
		key: "add_members_admins_only",
		label: "Only admins add members",
		help: "Members cannot add others.",
	},
	{
		key: "join_approval",
		label: "Admins approve new members",
		help: "Joining by link needs an admin's yes.",
	},
];

const g = ref(null);
const loading = ref(false);
const loadError = ref("");
const error = ref("");
const busy = ref("");
const tab = ref("settings");
const form = ref({});
const initial = ref("");
const toAdd = ref("");
const search = ref("");
const results = ref([]);

const ourNumber = computed(() => props.chat.whatsapp_number);
const canEdit = computed(() => g.value?.can_change && g.value?.we_are_admin);
const changed = computed(() => JSON.stringify(form.value) !== initial.value);
const shownMembers = computed(() => {
	const q = search.value.trim().toLowerCase();
	const list = g.value?.members || [];
	return q
		? list.filter((m) => (m.name || "").toLowerCase().includes(q) || m.number.includes(q))
		: list;
});

function fill(info) {
	g.value = info;
	form.value = {
		name: info.name,
		description: info.description,
		messages_admins_only: info.messages_admins_only,
		info_admins_only: info.info_admins_only,
		add_members_admins_only: info.add_members_admins_only,
		join_approval: false,
		ephemeral: "",
	};
	initial.value = JSON.stringify(form.value);
}

async function load() {
	loading.value = true;
	loadError.value = error.value = "";
	results.value = [];
	try {
		fill(await call(API + "info", { chat: props.chat.name }));
	} catch (e) {
		loadError.value = errorText(e, "Periskope did not answer for this group.");
	} finally {
		loading.value = false;
	}
}
watch(open, (o) => o && load(), { immediate: true });

async function save() {
	busy.value = "save";
	error.value = "";
	try {
		const before = JSON.parse(initial.value);
		const args = { chat: props.chat.name };
		for (const k of Object.keys(form.value)) {
			if (form.value[k] === before[k]) continue;
			args[k] = typeof form.value[k] === "boolean" ? (form.value[k] ? 1 : 0) : form.value[k];
		}
		fill(await call(API + "update", args));
		if (args.name) emit("renamed", args.name);
		toast.success("Group settings saved");
	} catch (e) {
		error.value = errorText(e, "The settings could not be saved.");
	} finally {
		busy.value = "";
	}
}

const LABEL = { add: "Add", remove: "Remove", promote: "Make admin", demote: "Remove as admin" };
function memberOptions(m) {
	return [
		m.is_admin
			? { label: LABEL.demote, onClick: () => act("demote", m.number, m) }
			: { label: LABEL.promote, onClick: () => act("promote", m.number, m) },
		{ label: "Remove from group", theme: "red", onClick: () => act("remove", m.number, m) },
	];
}

async function act(action, numbers, member) {
	if (action === "remove") {
		const ok = await ask(
			`${member?.name || "+" + numbers} will be removed from the group. The members see it.`,
			{
				title: "Remove from group?",
				confirmLabel: "Remove",
				danger: true,
			}
		);
		if (!ok) return;
	}
	busy.value = action;
	error.value = "";
	try {
		const list = String(numbers)
			.split(/[,\s]+/)
			.map((n) => n.replace(/\D/g, ""))
			.filter(Boolean);
		const r = await call(API + "members", {
			chat: props.chat.name,
			action,
			numbers: JSON.stringify(list),
		});
		results.value = r.results;
		fill(r.info);
		if (action === "add") toAdd.value = "";
	} catch (e) {
		error.value = errorText(e, "That did not work.");
	} finally {
		busy.value = "";
	}
}

async function resetInvite() {
	const ok = await ask("The current link stops working; anyone who has it can no longer join.", {
		title: "Reset the invite link?",
		confirmLabel: "Reset",
		danger: true,
	});
	if (!ok) return;
	busy.value = "invite";
	try {
		g.value.invite_link = (
			await call(API + "reset_invite", { chat: props.chat.name })
		).invite_link;
	} catch (e) {
		error.value = errorText(e, "The link could not be reset.");
	} finally {
		busy.value = "";
	}
}

async function leave() {
	const ok = await ask(
		"Our number leaves this WhatsApp group: its messages stop coming in, and joining again needs a new invite. The members see that we left. The chat and its history stay here, closed.",
		{ title: "Leave the group?", confirmLabel: "Leave the group", danger: true }
	);
	if (!ok) return;
	busy.value = "leave";
	try {
		await call(API + "leave", { chat: props.chat.name });
		open.value = false;
		toast.success("Left the group");
		emit("renamed");
	} catch (e) {
		error.value = errorText(e, "Could not leave the group.");
	} finally {
		busy.value = "";
	}
}

async function copy() {
	try {
		await navigator.clipboard.writeText(g.value.invite_link);
		toast.success("Link copied");
	} catch {
		error.value = "Copy the link by hand: the browser did not allow copying.";
	}
}
</script>
