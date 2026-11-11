<template>
	<Dialog v-model="open" :options="{ title: title, size: 'md' }">
		<template #body-content>
			<div class="flex flex-col items-center gap-3 text-center">
				<template v-if="connected">
					<span
						class="grid size-14 place-items-center rounded-full bg-surface-green-2 text-ink-green-8"
					>
						<LucideCheck class="size-7" />
					</span>
					<div class="text-base font-semibold text-ink-gray-9">Connected</div>
					<p v-if="add" class="text-sm text-ink-gray-6">
						{{ linkedNumber ? `+${linkedNumber}` : "The new number" }} is added. Set
						its team and rep in the list; its chats come into the inbox.
					</p>
					<p v-else class="text-sm text-ink-gray-6">
						{{ number?.display_name || number?.name }} is linked. Its chats come into
						the inbox again.
					</p>
				</template>

				<template v-else>
					<p class="text-sm text-ink-gray-6">
						On the phone {{ add ? "with the WhatsApp to add" : "of this number" }},
						open <b>WhatsApp → Settings → Linked devices → Link a device</b> and scan
						this code.
					</p>
					<div
						class="grid size-64 place-items-center rounded-xl border border-outline-gray-2 bg-white p-2"
					>
						<img
							v-if="qr"
							:src="qr"
							class="size-full object-contain"
							alt="WhatsApp QR code"
						/>
						<div
							v-else
							class="flex flex-col items-center gap-2 text-sm text-ink-gray-5"
						>
							<LoadingIndicator v-if="!expired" class="size-5" />
							{{
								expired
									? "The code expired."
									: adding
									? "Adding the phone in Periskope…"
									: "Getting the code from Periskope…"
							}}
						</div>
					</div>
					<div class="text-xs text-ink-gray-5">
						State: {{ state || "…"
						}}<template v-if="!expired"> · refreshes by itself</template>
					</div>
				</template>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<div class="flex w-full gap-2">
				<template v-if="connected">
					<Button class="w-full" variant="solid" label="Done" @click="open = false" />
				</template>
				<template v-else>
					<Button
						class="flex-1"
						label="Restart the number"
						:disabled="!target"
						:loading="restarting"
						@click="restart"
					/>
					<Button
						v-if="expired"
						class="flex-1"
						variant="solid"
						label="Show a new code"
						@click="start"
					/>
				</template>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Button, Dialog, ErrorMessage, LoadingIndicator, call } from "frappe-ui";
import LucideCheck from "~icons/lucide/check";
import { errorText } from "@/utils/errors";

// Link a Periskope number by scanning its QR code here (api/phones.py), instead of in the Periskope
// console. "relink" first logs the number's WhatsApp out; "add" first adds a new phone to the Periskope
// account (SettingsModal asks first for both). The code is asked for every few seconds (it changes)
// until the number is connected, for at most WAIT_MS.
const open = defineModel({ type: Boolean, default: false });
const props = defineProps({
	number: { type: Object, default: null }, // the WhatsApp Number row
	relink: { type: Boolean, default: false },
	add: { type: Boolean, default: false }, // a new phone: no number until it is scanned
});
const emit = defineEmits(["changed"]);

const API = "periskope_connector.api.phones.";
const POLL_MS = 3000;
const WAIT_MS = 3 * 60 * 1000;
const qr = ref(null);
const state = ref("");
const connected = ref(false);
const expired = ref(false);
const error = ref("");
const restarting = ref(false);
const adding = ref(false);
const phoneId = ref(null); // the new phone (add)
const linkedNumber = ref(null);
// what the API acts on: the WhatsApp Number, or the new phone by its Periskope id
const target = computed(() =>
	props.add ? phoneId.value && { phone_id: phoneId.value } : { number: props.number?.name }
);
let timer = null;
let started = 0;

const title = computed(() =>
	props.add
		? "Add a WhatsApp number"
		: `${props.relink ? "Re-link" : "Connect"} ${
				props.number?.display_name || props.number?.name || ""
		  }`
);

function stop() {
	clearTimeout(timer);
	timer = null;
}

async function poll() {
	try {
		const r = await call(API + "qr", target.value);
		if (!open.value) return;
		state.value = r.state;
		error.value = "";
		if (r.connected) {
			connected.value = true;
			linkedNumber.value = r.number;
			qr.value = null;
			emit("changed");
			return stop();
		}
		qr.value = r.qr || qr.value;
	} catch (e) {
		error.value = errorText(e, "Periskope did not answer.");
	}
	if (Date.now() - started > WAIT_MS) {
		expired.value = true;
		qr.value = null;
		return stop();
	}
	timer = setTimeout(poll, POLL_MS);
}

function start() {
	stop();
	expired.value = false;
	connected.value = false;
	qr.value = null;
	started = Date.now();
	poll();
}

watch(open, async (isOpen) => {
	if (!isOpen) return stop();
	error.value = "";
	state.value = "";
	connected.value = false;
	linkedNumber.value = null;
	if (props.add) {
		phoneId.value = null;
		adding.value = true;
		try {
			phoneId.value = (await call(API + "add")).phone_id;
		} catch (e) {
			error.value = errorText(e, "Periskope could not add the phone.");
			return;
		} finally {
			adding.value = false;
		}
	} else if (props.relink) {
		try {
			await call(API + "relink", { number: props.number.name });
			emit("changed");
		} catch (e) {
			error.value = errorText(e, "Periskope could not log the number out.");
			return;
		}
	}
	start();
});

async function restart() {
	restarting.value = true;
	error.value = "";
	try {
		await call(API + "restart", target.value);
		start();
	} catch (e) {
		error.value = errorText(e, "Periskope could not restart the number.");
	} finally {
		restarting.value = false;
	}
}

onBeforeUnmount(stop);
</script>
