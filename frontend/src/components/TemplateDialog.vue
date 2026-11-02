<template>
	<Dialog v-model="show" :options="{ title: 'Send a template', size: 'xl' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<p class="text-sm text-ink-gray-6">
					Meta takes a free message only within 24 hours of the customer's last message.
					An approved template can be sent any time.
				</p>
				<TemplatePicker v-model="choice" :templates="templates" :loading="loading" />
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				label="Send template"
				:loading="sending"
				:disabled="!choice.ready || sending"
				@click="send"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { ref, watch } from "vue";
import { Button, Dialog, ErrorMessage, call } from "frappe-ui";
import TemplatePicker from "@/components/TemplatePicker.vue";
import { errorText } from "@/utils/errors";

// The approved templates of a Meta WhatsApp chat (api/compose.meta_templates), sent with
// api/compose.send_template.
const show = defineModel({ type: Boolean, default: false });
const props = defineProps({ chat: { type: Object, required: true } });
const emit = defineEmits(["sent"]);

const templates = ref([]);
const loading = ref(false);
const sending = ref(false);
const error = ref("");
const choice = ref({});

watch(show, async (open) => {
	if (!open) return;
	error.value = "";
	loading.value = true;
	try {
		templates.value = await call("periskope_connector.api.compose.meta_templates", {
			chat: props.chat.name,
		});
	} catch (e) {
		error.value = errorText(e, "Could not load the templates.");
	} finally {
		loading.value = false;
	}
});

async function send() {
	sending.value = true;
	error.value = "";
	try {
		const message = await call("periskope_connector.api.compose.send_template", {
			chat: props.chat.name,
			template: choice.value.template,
			params: choice.value.params,
		});
		emit("sent", message);
		show.value = false;
	} catch (e) {
		error.value = errorText(e, "The template was not sent.");
	} finally {
		sending.value = false;
	}
}
</script>
