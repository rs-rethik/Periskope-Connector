<template>
	<Dialog v-model="open" :options="{ title: 'New WhatsApp chat', size: isMeta ? 'xl' : 'md' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<FormControl
					v-model="number"
					type="select"
					label="Send from"
					:options="
						numbers.map((n) => ({
							label: n.source === 'Meta' ? `${n.label} (Meta)` : n.label,
							value: n.name,
						}))
					"
				/>
				<FormControl
					v-model="phone"
					label="Customer's WhatsApp number"
					autofocus
					placeholder="919876543210"
					description="With the country code. A 10-digit number gets the company's country code."
				/>

				<!-- Meta WhatsApp: only an approved template, until the customer has written -->
				<template v-if="isMeta">
					<p class="text-sm text-ink-gray-6">
						A Meta WhatsApp number can start a chat only with an approved template.
						Free messages and files work once the customer has replied.
					</p>
					<TemplatePicker
						v-model="choice"
						:templates="templates"
						:loading="loadingTemplates"
					/>
				</template>

				<template v-else>
					<FormControl v-model="text" type="textarea" label="Message" :rows="4" />
					<div class="flex items-center gap-2">
						<Button
							size="sm"
							:label="file ? 'Change file' : 'Attach a file'"
							:loading="uploading"
							@click="$refs.picker.click()"
						>
							<template #prefix><LucidePaperclip class="size-3.5" /></template>
						</Button>
						<input ref="picker" type="file" class="hidden" @change="onFile" />
						<template v-if="file">
							<span class="min-w-0 truncate text-sm text-ink-gray-7">{{
								file.file_name
							}}</span>
							<Tooltip text="Remove file"
								><button
									class="shrink-0 text-ink-gray-5 hover:text-ink-gray-8"
									aria-label="Remove file"
									@click="file = null"
								>
									<LucideX class="size-4" /></button
							></Tooltip>
						</template>
					</div>
				</template>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				variant="solid"
				:label="isMeta ? 'Send template' : 'Send'"
				:loading="sending"
				:disabled="!canSend"
				@click="send"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Button, Dialog, ErrorMessage, FormControl, Tooltip, call, toast } from "frappe-ui";
import LucidePaperclip from "~icons/lucide/paperclip";
import LucideX from "~icons/lucide/x";
import TemplatePicker from "@/components/TemplatePicker.vue";
import { boot } from "@/boot";
import { inbox } from "@/stores/inbox";
import { errorText } from "@/utils/errors";
import { uploadPrivate } from "@/utils/upload";

// Write first to a customer: a text and/or a file from a Periskope number, or an approved template
// from a Meta WhatsApp number (api/compose.start_chat).
const open = defineModel({ type: Boolean, default: false });
const router = useRouter();

const numbers = computed(() => boot.numbers.filter((n) => n.is_active));
const number = ref("");
const phone = ref("");
const text = ref("");
const file = ref(null);
const uploading = ref(false);
const error = ref("");
const sending = ref(false);
const templates = ref([]);
const loadingTemplates = ref(false);
const choice = ref({});

const isMeta = computed(
	() => numbers.value.find((n) => n.name === number.value)?.source === "Meta"
);
const canSend = computed(() => {
	if (!number.value || !phone.value.trim() || sending.value || uploading.value) return false;
	return isMeta.value ? !!choice.value.ready : !!text.value.trim() || !!file.value;
});

watch(open, (v) => {
	if (!v) return;
	number.value =
		inbox.number ||
		numbers.value.find((n) => n.is_ready)?.name ||
		numbers.value[0]?.name ||
		"";
	error.value = "";
});

// A Meta number's templates are loaded when it is chosen.
watch([number, open], async () => {
	if (!open.value || !isMeta.value) return;
	loadingTemplates.value = true;
	try {
		templates.value = await call("periskope_connector.api.compose.meta_templates", {
			number: number.value,
		});
	} catch (e) {
		error.value = errorText(e, "Could not load the templates.");
	} finally {
		loadingTemplates.value = false;
	}
});

async function onFile(e) {
	const picked = e.target.files?.[0];
	e.target.value = "";
	if (!picked) return;
	uploading.value = true;
	error.value = "";
	try {
		file.value = await uploadPrivate(picked);
	} catch (err) {
		file.value = null;
		error.value = err.message;
	} finally {
		uploading.value = false;
	}
}

async function send() {
	if (!canSend.value) return;
	sending.value = true;
	error.value = "";
	try {
		const r = await call(
			"periskope_connector.api.compose.start_chat",
			isMeta.value
				? {
						number: number.value,
						phone: phone.value,
						template: choice.value.template,
						params: choice.value.params,
				  }
				: {
						number: number.value,
						phone: phone.value,
						text: text.value,
						file: file.value?.name,
				  }
		);
		// The chat exists now either way: open it. A message that did not go out shows there with
		// its reason and a Retry button, so it is never sent twice from this dialog.
		if (["Failed", "Not Sent"].includes(r.message.send_status))
			toast.error(r.message.send_error || "The message was not sent.");
		open.value = false;
		phone.value = "";
		text.value = "";
		file.value = null;
		router.push({ name: "Inbox", params: { chat: r.chat } });
	} catch (e) {
		error.value = errorText(e, "Not sent.");
	} finally {
		sending.value = false;
	}
}
</script>
