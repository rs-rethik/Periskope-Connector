<template>
	<Dialog v-model="open" :options="{ title: 'New broadcast', size: '3xl' }">
		<template #body-content>
			<div class="grid gap-4 md:grid-cols-2">
				<!-- who -->
				<div class="flex min-w-0 flex-col gap-2">
					<FormControl
						v-model="form.number"
						type="select"
						label="Send from"
						:options="numberOptions"
					/>
					<div class="grid grid-cols-2 gap-2">
						<FormControl
							v-model="filters.label"
							type="select"
							:options="labelOptions"
						/>
						<FormControl
							v-model="filters.linked"
							type="select"
							:options="[
								{ label: 'In the CRM or not', value: '' },
								{ label: 'In the CRM', value: '1' },
								{ label: 'Not in the CRM', value: '0' },
							]"
						/>
						<FormControl v-model="filters.search" placeholder="Search" />
					</div>
					<div class="flex items-center justify-between text-xs text-ink-gray-6">
						<span
							>{{ picked.length }} of {{ chats.length
							}}{{ more ? "+" : "" }} picked<template v-if="leftToday !== null">
								· {{ leftToday }} left today on this number</template
							></span
						>
						<span class="flex gap-2">
							<button class="hover:text-ink-gray-9" @click="pickAll">
								Pick all
							</button>
							<button class="hover:text-ink-gray-9" @click="picked = []">
								Clear
							</button>
						</span>
					</div>
					<div
						class="h-64 divide-y divide-outline-gray-1 overflow-y-auto rounded-lg border border-outline-gray-2"
					>
						<label
							v-for="c in chats"
							:key="c.name"
							class="flex items-center gap-2 px-3 py-1.5 text-sm text-ink-gray-8"
							:class="
								c.allowed ? 'cursor-pointer hover:bg-surface-gray-1' : 'opacity-50'
							"
						>
							<input
								v-model="picked"
								type="checkbox"
								:value="c.name"
								:disabled="!c.allowed"
								class="rounded"
							/>
							<span class="min-w-0 flex-1 truncate">{{ c.title }}</span>
							<span v-if="c.blocked" class="shrink-0 text-xs text-ink-amber-7">{{
								c.blocked
							}}</span>
						</label>
						<p
							v-if="!loading && !chats.length"
							class="px-3 py-6 text-center text-sm text-ink-gray-5"
						>
							No chats match.
						</p>
					</div>
				</div>

				<!-- what and when -->
				<div class="flex min-w-0 flex-col gap-3">
					<FormControl
						v-model="form.title"
						label="Name (for this list only)"
						placeholder="e.g. Diwali greetings"
					/>
					<div class="flex flex-col gap-1">
						<FormControl
							v-model="form.message"
							type="textarea"
							label="Message"
							:rows="6"
						/>
						<button
							class="self-start text-xs text-ink-blue-7 hover:underline"
							@click="form.message += '{{name}}'"
						>
							Insert the chat's name: {{ "{" + "{name}" + "}" }}
						</button>
					</div>
					<div class="flex items-center gap-2 text-sm text-ink-gray-8">
						<Button size="sm" :loading="uploading" @click="$refs.picker.click()">
							<template #prefix><LucidePaperclip class="size-3.5" /></template>
							{{ file ? "Change file" : "Attach a file" }}
						</Button>
						<span v-if="file" class="truncate text-ink-gray-6">{{
							file.file_name
						}}</span>
						<input ref="picker" type="file" class="hidden" @change="onFile" />
					</div>
					<div class="flex flex-col gap-1.5">
						<label class="flex items-center gap-2 text-sm text-ink-gray-8">
							<input v-model="later" type="checkbox" class="rounded" /> Start later
						</label>
						<DateTimePicker
							v-if="later"
							v-model="form.scheduled_at"
							placeholder="Start at"
						/>
					</div>
					<FormControl
						v-model="form.delay"
						type="select"
						label="Gap between messages"
						:options="[
							{ label: '5 seconds', value: '5' },
							{ label: '8 seconds (suggested)', value: '8' },
							{ label: '15 seconds', value: '15' },
							{ label: '30 seconds', value: '30' },
							{ label: '60 seconds', value: '60' },
						]"
					/>
				</div>
			</div>
			<p class="mt-4 rounded-lg bg-surface-amber-1 px-3 py-2 text-xs text-ink-amber-8">
				Each chat uses one Periskope broadcast credit. Send only what customers expect, or
				WhatsApp may block the number for spam.
				<template v-if="boot.test_mode">
					Test mode is on: only the test numbers can be picked.</template
				>
			</p>
			<ErrorMessage class="mt-2" :message="error" />
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				:label="
					picked.length
						? `Send to ${picked.length} ${picked.length === 1 ? 'chat' : 'chats'}`
						: 'Send'
				"
				:loading="sending"
				:disabled="
					!picked.length ||
					(!form.message.trim() && !file) ||
					uploading ||
					(later && !form.scheduled_at)
				"
				@click="send"
			>
				<template #prefix><LucideMegaphone class="size-4" /></template>
			</Button>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { Button, DateTimePicker, Dialog, ErrorMessage, FormControl, call, toast } from "frappe-ui";
import LucidePaperclip from "~icons/lucide/paperclip";
import LucideMegaphone from "~icons/lucide/megaphone";
import { boot } from "@/boot";
import { ask } from "@/utils/confirm";
import { errorText } from "@/utils/errors";
import { uploadPrivate } from "@/utils/upload";

// One message to many chats of a Periskope number (managers; api/broadcast.py).
const open = defineModel({ type: Boolean, default: false });
const emit = defineEmits(["sent"]);
const API = "periskope_connector.api.broadcast.";

const numbers = computed(() => boot.numbers.filter((n) => n.is_active && n.source !== "Meta"));
const numberOptions = computed(() =>
	numbers.value.map((n) => ({ label: n.label, value: n.name }))
);
const labelOptions = computed(() => [
	{ label: "Any label", value: "" },
	...(boot.tags || []).map((t) => ({ label: t, value: t })),
]);
const blank = () => ({ number: "", title: "", message: "", scheduled_at: "", delay: "8" });
const form = ref(blank());
const filters = ref({ label: "", linked: "", groups: "0", search: "" }); // never groups
const leftToday = ref(null);
const chats = ref([]);
const more = ref(false);
const picked = ref([]);
const loading = ref(false);
const later = ref(false);
const file = ref(null);
const uploading = ref(false);
const sending = ref(false);
const error = ref("");

let timer;
async function load() {
	if (!form.value.number) return;
	loading.value = true;
	try {
		const f = Object.fromEntries(Object.entries(filters.value).filter(([, v]) => v !== ""));
		const r = await call(API + "recipients", {
			number: form.value.number,
			filters: JSON.stringify(f),
		});
		chats.value = r.chats;
		more.value = r.more;
		leftToday.value = r.left_today;
		const allowed = new Set(r.chats.filter((c) => c.allowed).map((c) => c.name));
		picked.value = picked.value.filter((n) => allowed.has(n));
	} catch (e) {
		error.value = errorText(e, "Could not load the chats.");
	} finally {
		loading.value = false;
	}
}
watch(
	[filters, () => form.value.number],
	() => {
		clearTimeout(timer);
		timer = setTimeout(load, 250);
	},
	{ deep: true }
);
watch(open, (o) => {
	if (!o) return;
	form.value = {
		...blank(),
		number: numbers.value.find((n) => n.is_ready)?.name || numbers.value[0]?.name || "",
	};
	picked.value = [];
	file.value = null;
	later.value = false;
	error.value = "";
});

function pickAll() {
	const room = leftToday.value ?? Infinity;
	picked.value = chats.value
		.filter((c) => c.allowed)
		.slice(0, room)
		.map((c) => c.name);
}

async function onFile(e) {
	const f = e.target.files?.[0];
	e.target.value = "";
	if (!f) return;
	uploading.value = true;
	try {
		file.value = await uploadPrivate(f);
	} catch (err) {
		error.value = err.message || "The file could not be uploaded.";
	} finally {
		uploading.value = false;
	}
}

async function send() {
	const n = picked.value.length;
	const ok = await ask(
		`${later.value ? "It starts at the chosen time and goes" : "It goes"} to ${n} ${
			n === 1 ? "chat" : "chats"
		}, one every ${form.value.delay} seconds, and uses ${n} broadcast ${
			n === 1 ? "credit" : "credits"
		}.`,
		{ title: "Send this broadcast?", confirmLabel: "Send" }
	);
	if (!ok) return;
	sending.value = true;
	error.value = "";
	try {
		const b = await call(API + "send", {
			number: form.value.number,
			chats: JSON.stringify(picked.value),
			message: form.value.message,
			file: file.value?.name || null,
			title: form.value.title,
			scheduled_at: later.value ? form.value.scheduled_at : null,
			delay: form.value.delay,
		});
		toast.success(later.value ? "Broadcast scheduled" : "Broadcast started");
		emit("sent", b);
		open.value = false;
	} catch (e) {
		error.value = errorText(e, "The broadcast was not sent.");
	} finally {
		sending.value = false;
	}
}
</script>
