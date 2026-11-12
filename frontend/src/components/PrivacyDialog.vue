<template>
	<Dialog v-model="open" :options="{ title: 'This customer\'s data', size: 'md' }">
		<template #body-content>
			<div v-if="!info" class="flex justify-center p-4">
				<LoadingIndicator class="size-5" />
			</div>
			<div v-else class="flex flex-col gap-3 text-sm text-ink-gray-8">
				<p>
					For +{{ info.number }} the app keeps {{ info.messages }} message(s) in
					{{ info.chats }} chat(s) and {{ info.calls }} call(s).
				</p>
				<Button label="Download it all (zip)" @click="download">
					<template #prefix><LucideDownload class="size-4" /></template>
				</Button>
				<div class="flex flex-col gap-2 rounded-lg border border-outline-red-2 p-3">
					<p class="font-medium text-ink-red-8">Erase it</p>
					<p class="text-xs text-ink-gray-6">
						Deletes the chats, messages, files, calls and what the AI wrote from them,
						for good.
						<template v-if="info.lead_or_deal">
							The CRM lead or deal stays: erase it in the CRM if the request covers
							it.
						</template>
					</p>
					<FormControl
						v-model="reason"
						type="textarea"
						:rows="2"
						label="Why (kept in the erasure log)"
					/>
					<FormControl
						v-model="confirm"
						:label="`Type ${info.number.slice(-4)} to confirm`"
					/>
					<Button
						theme="red"
						variant="solid"
						label="Erase this customer's WhatsApp data"
						:disabled="confirm !== info.number.slice(-4) || !reason.trim()"
						:loading="busy"
						@click="erase"
					/>
					<ErrorMessage :message="error" />
				</div>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
	Button,
	Dialog,
	ErrorMessage,
	FormControl,
	LoadingIndicator,
	call,
	toast,
} from "frappe-ui";
import LucideDownload from "~icons/lucide/download";
import { errorText } from "@/utils/errors";

// A privacy request (api/privacy.py): download or erase one number's WhatsApp data. Managers only.
const props = defineProps({ number: { type: String, required: true } });
const open = defineModel({ type: Boolean, default: false });
const router = useRouter();
const API = "periskope_connector.api.privacy.";
const info = ref(null);
const reason = ref("");
const confirm = ref("");
const busy = ref(false);
const error = ref("");

watch(
	open,
	async (o) => {
		if (!o) return;
		info.value = null;
		reason.value = confirm.value = error.value = "";
		try {
			info.value = await call(API + "summary", { number: props.number });
		} catch (e) {
			toast.error(errorText(e, "Could not read this customer's data."));
			open.value = false;
		}
	},
	{ immediate: true }
);

function download() {
	window.open(
		`/api/method/${API}export?number=${encodeURIComponent(info.value.number)}`,
		"_blank"
	);
}

async function erase() {
	busy.value = true;
	error.value = "";
	try {
		await call(API + "erase", {
			number: info.value.number,
			reason: reason.value,
			confirm: confirm.value,
		});
		toast.success("Erased");
		open.value = false;
		router.push({ name: "Inbox", params: {} });
	} catch (e) {
		error.value = errorText(e, "Could not erase.");
	} finally {
		busy.value = false;
	}
}
</script>
