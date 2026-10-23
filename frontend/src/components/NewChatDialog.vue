<template>
	<Dialog v-model="open" :options="{ title: 'New WhatsApp chat' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<FormControl
					v-model="number"
					type="select"
					label="Send from"
					:options="numbers.map((n) => ({ label: n.label, value: n.name }))"
				/>
				<FormControl
					v-model="phone"
					label="Customer's WhatsApp number"
					placeholder="919876543210"
					description="With the country code. A 10-digit number is taken as Indian (+91)."
				/>
				<FormControl v-model="text" type="textarea" label="Message" :rows="4" />
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				variant="solid"
				label="Send"
				:loading="sending"
				:disabled="!number || !phone || !text.trim()"
				@click="send"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Button, Dialog, ErrorMessage, FormControl, call } from "frappe-ui";
import { boot } from "@/boot";
import { inbox } from "@/stores/inbox";

const open = defineModel({ type: Boolean, default: false });
const router = useRouter();

const numbers = computed(() => boot.numbers.filter((n) => n.is_active));
const number = ref("");
const phone = ref("");
const text = ref("");
const error = ref("");
const sending = ref(false);

watch(open, (v) => {
	if (!v) return;
	number.value =
		inbox.number ||
		numbers.value.find((n) => n.is_ready)?.name ||
		numbers.value[0]?.name ||
		"";
	error.value = "";
});

async function send() {
	sending.value = true;
	error.value = "";
	try {
		const r = await call("periskope_connector.api.compose.start_chat", {
			number: number.value,
			phone: phone.value,
			text: text.value,
		});
		if (["Failed", "Not Sent"].includes(r.message.send_status))
			error.value = r.message.send_error;
		else {
			open.value = false;
			phone.value = "";
			text.value = "";
		}
		router.push({ name: "Inbox", params: { chat: r.chat } });
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "Not sent.";
	} finally {
		sending.value = false;
	}
}
</script>
