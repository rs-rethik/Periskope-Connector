<template>
	<Dialog v-model="open" :options="{ title: 'New WhatsApp group', size: 'lg' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<FormControl
					v-model="form.number"
					type="select"
					label="Make it on"
					:options="numberOptions"
				/>
				<FormControl
					v-model="form.name"
					label="Group name"
					placeholder="e.g. Sri Murugan Spinners ~ Pranera"
				/>
				<FormControl
					v-model="form.members"
					type="textarea"
					label="Members"
					:rows="3"
					placeholder="Phone numbers, one per line or comma separated (a 10-digit number gets the company's country code)"
				/>
				<p class="-mt-2 text-xs text-ink-gray-5">
					{{ people.length }} {{ people.length === 1 ? "member" : "members" }}. Our
					number is the group's admin. Who WhatsApp will not add directly gets an invite.
				</p>
				<FormControl
					v-model="form.description"
					type="textarea"
					label="Description (optional)"
					:rows="2"
				/>
				<div
					class="flex flex-col gap-2 rounded-xl border border-outline-gray-2 p-3 text-sm text-ink-gray-8"
				>
					<label
						v-for="s in switches"
						:key="s.key"
						class="flex items-center justify-between gap-3"
					>
						<span class="text-ink-gray-8">{{ s.label }}</span>
						<Switch v-model="form[s.key]" />
					</label>
				</div>
				<p
					v-if="boot.test_mode"
					class="rounded-lg bg-surface-amber-1 px-3 py-2 text-xs text-ink-amber-8"
				>
					Test mode is on: a group can only have test numbers.
				</p>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				label="Make the group"
				:loading="saving"
				:disabled="!form.number || !form.name.trim() || !people.length"
				@click="create"
			>
				<template #prefix><LucideUsersRound class="size-4" /></template>
			</Button>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Button, Dialog, ErrorMessage, FormControl, Switch, call, toast } from "frappe-ui";
import LucideUsersRound from "~icons/lucide/users-round";
import { boot } from "@/boot";
import { errorText } from "@/utils/errors";

// Make a WhatsApp group on a Periskope number (managers; api/group_settings.create), then open it.
const open = defineModel({ type: Boolean, default: false });
const router = useRouter();

const switches = [
	{ key: "messages_admins_only", label: "Only admins send messages" },
	{ key: "info_admins_only", label: "Only admins edit group info" },
	{ key: "add_members_admins_only", label: "Only admins add members" },
];
const numbers = computed(() => boot.numbers.filter((n) => n.is_active && n.source !== "Meta"));
const numberOptions = computed(() =>
	numbers.value.map((n) => ({ label: n.label, value: n.name }))
);
const blank = () => ({
	number: "",
	name: "",
	members: "",
	description: "",
	messages_admins_only: false,
	info_admins_only: true,
	add_members_admins_only: true,
});
const form = ref(blank());
const error = ref("");
const saving = ref(false);

const people = computed(() => [
	...new Set(
		form.value.members
			.split(/[\n,;]+/)
			.map((p) => p.replace(/\D/g, ""))
			.filter((p) => p.length >= 10)
	),
]);

// (run at once too: the dialog is loaded on demand and may mount already open)
watch(
	open,
	(o) => {
		if (!o) return;
		form.value = {
			...blank(),
			number: numbers.value.find((n) => n.is_ready)?.name || numbers.value[0]?.name || "",
		};
		error.value = "";
	},
	{ immediate: true }
);

async function create() {
	saving.value = true;
	error.value = "";
	try {
		const r = await call("periskope_connector.api.group_settings.create", {
			number: form.value.number,
			name: form.value.name,
			participants: JSON.stringify(people.value),
			description: form.value.description,
			messages_admins_only: form.value.messages_admins_only ? 1 : 0,
			info_admins_only: form.value.info_admins_only ? 1 : 0,
			add_members_admins_only: form.value.add_members_admins_only ? 1 : 0,
		});
		open.value = false;
		toast.success("Group made");
		router.push({ name: "Inbox", params: { chat: r.chat } });
	} catch (e) {
		error.value = errorText(e, "The group could not be made.");
	} finally {
		saving.value = false;
	}
}
</script>
