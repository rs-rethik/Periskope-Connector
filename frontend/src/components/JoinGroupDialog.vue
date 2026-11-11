<template>
	<Dialog v-model="open" :options="{ title: 'Join a group', size: 'md' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<FormControl
					v-model="form.number"
					type="select"
					label="Join with"
					:options="numberOptions"
				/>
				<FormControl
					v-model="form.link"
					label="Invite link"
					placeholder="https://chat.whatsapp.com/…"
					autofocus
				/>
				<p class="text-xs text-ink-gray-5">
					The group's members see that our number joined. Its chats then come into the
					inbox.
				</p>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				label="Join the group"
				:loading="saving"
				:disabled="!form.number || !form.link.trim()"
				@click="join"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Button, Dialog, ErrorMessage, FormControl, call, toast } from "frappe-ui";
import { boot } from "@/boot";
import { errorText } from "@/utils/errors";

// Our Periskope number joins a WhatsApp group by its invite link (managers; api/group_settings.join).
const open = defineModel({ type: Boolean, default: false });
const router = useRouter();

const numbers = computed(() => boot.numbers.filter((n) => n.is_active && n.source !== "Meta"));
const numberOptions = computed(() =>
	numbers.value.map((n) => ({ label: n.label, value: n.name }))
);
const form = ref({ number: "", link: "" });
const error = ref("");
const saving = ref(false);

// (run at once too: the dialog is loaded on demand and may mount already open)
watch(
	open,
	(o) => {
		if (!o) return;
		form.value = {
			number: numbers.value.find((n) => n.is_ready)?.name || numbers.value[0]?.name || "",
			link: "",
		};
		error.value = "";
	},
	{ immediate: true }
);

async function join() {
	saving.value = true;
	error.value = "";
	try {
		const r = await call("periskope_connector.api.group_settings.join", form.value);
		open.value = false;
		toast.success("Joined the group");
		router.push({ name: "Inbox", params: { chat: r.chat } });
	} catch (e) {
		error.value = errorText(e, "The group could not be joined.");
	} finally {
		saving.value = false;
	}
}
</script>
