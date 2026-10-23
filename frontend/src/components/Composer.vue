<template>
	<div class="border-t border-outline-gray-1 bg-surface-white">
		<div class="flex items-center gap-1 px-3 pt-2">
			<Button
				size="sm"
				:variant="mode === 'reply' ? 'subtle' : 'ghost'"
				label="Reply"
				@click="setMode('reply')"
			/>
			<Button
				size="sm"
				:variant="mode === 'note' ? 'subtle' : 'ghost'"
				label="Private note"
				@click="setMode('note')"
			/>
			<span class="ml-auto truncate text-xs text-ink-gray-5">
				{{
					mode === "note"
						? "Only your team sees notes. Type @ to mention someone."
						: `Sends from ${chat.number_label}`
				}}
			</span>
		</div>

		<div
			v-if="replyTo && mode === 'reply'"
			class="mx-3 mt-2 flex items-start gap-2 rounded border-l-4 border-outline-gray-3 bg-surface-gray-1 px-2 py-1 text-sm"
		>
			<div class="min-w-0 flex-1">
				<div class="text-xs font-medium text-ink-gray-7">
					Replying to
					{{
						replyTo.direction === "Outgoing"
							? "your message"
							: replyTo.sender_label || "customer"
					}}
				</div>
				<div class="truncate text-ink-gray-6">
					{{ replyTo.body || "[" + replyTo.message_type + "]" }}
				</div>
			</div>
			<button
				class="text-ink-gray-5 hover:text-ink-gray-8"
				title="Cancel reply"
				@click="$emit('clear-reply')"
			>
				<LucideX class="size-4" />
			</button>
		</div>

		<div
			v-if="file"
			class="mx-3 mt-2 flex items-center gap-2 rounded bg-surface-gray-1 px-2 py-1 text-sm"
		>
			<LucidePaperclip class="size-4 text-ink-gray-6" />
			<span class="truncate text-ink-gray-8">{{ file.file_name }}</span>
			<span v-if="uploading" class="text-xs text-ink-gray-5">uploading…</span>
			<button
				class="ml-auto text-ink-gray-5 hover:text-ink-gray-8"
				title="Remove file"
				@click="file = null"
			>
				<LucideX class="size-4" />
			</button>
		</div>

		<div class="relative px-3 py-2" :class="mode === 'note' ? 'bg-surface-amber-1' : ''">
			<div
				v-if="suggestions.length"
				class="absolute bottom-full left-3 z-10 mb-1 max-h-60 w-80 overflow-y-auto rounded-lg border border-outline-gray-2 bg-surface-white p-1 shadow-lg"
			>
				<button
					v-for="(s, i) in suggestions"
					:key="s.key"
					class="flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
					:class="i === active ? 'bg-surface-gray-2' : ''"
					@mousedown.prevent="pick(s)"
				>
					<span class="text-sm font-medium text-ink-gray-8">{{ s.label }}</span>
					<span v-if="s.hint" class="truncate text-xs text-ink-gray-5">{{
						s.hint
					}}</span>
				</button>
			</div>
			<textarea
				ref="box"
				v-model="text"
				rows="1"
				class="max-h-48 min-h-[40px] w-full resize-none rounded border-0 bg-transparent p-1 text-base text-ink-gray-9 placeholder-ink-gray-4 focus:ring-0"
				:placeholder="
					mode === 'note'
						? 'Write a note for your team…'
						: 'Type a message. / for quick replies'
				"
				@input="onInput"
				@keydown="onKey"
			/>
			<div class="flex items-center gap-1">
				<Popover v-if="mode === 'reply'">
					<template #target="{ togglePopover }">
						<Button variant="ghost" title="Emoji" @click="togglePopover()"
							><template #icon><LucideSmile class="size-4" /></template
						></Button>
					</template>
					<template #body="{ close }">
						<div
							class="grid w-72 grid-cols-8 gap-1 rounded-lg border border-outline-gray-2 bg-surface-white p-2 shadow-lg"
						>
							<button
								v-for="e in EMOJI"
								:key="e"
								class="rounded p-1 text-lg hover:bg-surface-gray-2"
								@click="
									insert(e);
									close();
								"
							>
								{{ e }}
							</button>
						</div>
					</template>
				</Popover>
				<Button
					v-if="mode === 'reply'"
					variant="ghost"
					title="Attach a file"
					@click="$refs.picker.click()"
				>
					<template #icon><LucidePaperclip class="size-4" /></template>
				</Button>
				<input ref="picker" type="file" class="hidden" @change="onFile" />
				<Dropdown v-if="mode === 'reply'" :options="quickOptions">
					<Button variant="ghost" title="Quick replies"
						><template #icon><LucideZap class="size-4" /></template
					></Button>
				</Dropdown>
				<span v-if="error" class="ml-2 truncate text-sm text-ink-red-4">{{ error }}</span>
				<Button
					class="ml-auto"
					variant="solid"
					:theme="mode === 'note' ? 'orange' : 'gray'"
					:label="mode === 'note' ? 'Add note' : 'Send'"
					:loading="sending"
					:disabled="uploading || (!text.trim() && !file)"
					@click="submit"
				>
					<template #suffix><LucideSendHorizontal class="size-4" /></template>
				</Button>
			</div>
		</div>

		<Dialog v-model="saveDialog" :options="{ title: 'Save as quick reply' }">
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl v-model="newQuick.title" label="Title" />
					<FormControl v-model="newQuick.body" type="textarea" label="Message" />
					<FormControl
						v-if="boot.is_manager"
						v-model="newQuick.is_shared"
						type="checkbox"
						label="Share with everyone"
					/>
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Save"
					:disabled="!newQuick.title || !newQuick.body"
					@click="saveQuick"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { Button, Dialog, Dropdown, FormControl, Popover, call } from "frappe-ui";
import LucideX from "~icons/lucide/x";
import LucidePaperclip from "~icons/lucide/paperclip";
import LucideSmile from "~icons/lucide/smile";
import LucideZap from "~icons/lucide/zap";
import LucideSendHorizontal from "~icons/lucide/send-horizontal";
import { boot } from "@/boot";

const props = defineProps({
	chat: { type: Object, required: true },
	replyTo: { type: Object, default: null },
});
const emit = defineEmits(["sent", "noted", "clear-reply"]);

const EMOJI = [
	"😀",
	"😊",
	"🙏",
	"👍",
	"👌",
	"🙂",
	"😅",
	"😂",
	"❤️",
	"🎉",
	"✅",
	"❌",
	"⏳",
	"📦",
	"🚚",
	"💰",
	"📄",
	"📞",
	"👋",
	"🤝",
	"⭐",
	"🔥",
	"😔",
	"😢",
	"🙌",
	"💯",
	"📍",
	"🕒",
	"☎️",
	"✉️",
	"🧵",
	"👕",
];

const mode = ref("reply");
const text = ref("");
const file = ref(null);
const uploading = ref(false);
const sending = ref(false);
const error = ref("");
const box = ref(null);
const quick = ref([]);
const people = ref([]);
const mentions = ref([]);
const suggestions = ref([]);
const active = ref(0);
const saveDialog = ref(false);
const newQuick = ref({ title: "", body: "", is_shared: false });
let trigger = null; // { kind: 'quick' | 'mention', start }

const quickOptions = computed(() => [
	...quick.value.map((q) => ({ label: q.title, onClick: () => insert(q.body, true) })),
	...(quick.value.length ? [{ label: "────────", disabled: true }] : []),
	{ label: "Save this text as a quick reply", onClick: openSave },
]);

function setMode(m) {
	mode.value = m;
	error.value = "";
	suggestions.value = [];
	nextTick(() => box.value?.focus());
}

function autosize() {
	const el = box.value;
	if (!el) return;
	el.style.height = "auto";
	el.style.height = Math.min(el.scrollHeight, 192) + "px";
}

function insert(value, replace = false) {
	if (replace && (!text.value.trim() || text.value.trim().startsWith("/"))) text.value = value;
	else
		text.value = text.value
			? `${text.value}${text.value.endsWith(" ") ? "" : " "}${value}`
			: value;
	nextTick(() => {
		autosize();
		box.value?.focus();
	});
}

// "/" at the start lists quick replies; "@" in a note lists teammates.
function onInput() {
	autosize();
	error.value = "";
	const el = box.value;
	const before = text.value.slice(0, el.selectionStart);
	const slash = mode.value === "reply" && /^\/(\S*)$/.exec(before);
	const at = mode.value === "note" && /(?:^|\s)@([\p{L}\p{N}.]*)$/u.exec(before);
	if (slash) {
		const q = slash[1].toLowerCase();
		trigger = { kind: "quick", start: 0 };
		suggestions.value = quick.value
			.filter(
				(r) => !q || r.title.toLowerCase().includes(q) || r.body.toLowerCase().includes(q)
			)
			.slice(0, 8)
			.map((r) => ({ key: r.name, label: r.title, hint: r.body, value: r.body }));
	} else if (at) {
		const q = at[1].toLowerCase();
		trigger = { kind: "mention", start: el.selectionStart - at[1].length - 1 };
		suggestions.value = people.value
			.filter(
				(p) =>
					p.name !== boot.user &&
					(!q || (p.full_name || p.name).toLowerCase().includes(q))
			)
			.slice(0, 8)
			.map((p) => ({ key: p.name, label: p.full_name || p.name, hint: p.name, value: p }));
	} else {
		suggestions.value = [];
	}
	active.value = 0;
}

function pick(s) {
	if (trigger?.kind === "quick") {
		text.value = s.value;
	} else if (trigger?.kind === "mention") {
		const el = box.value;
		const label = `@${s.label} `;
		text.value =
			text.value.slice(0, trigger.start) + label + text.value.slice(el.selectionStart);
		if (!mentions.value.includes(s.value.name)) mentions.value.push(s.value.name);
	}
	suggestions.value = [];
	nextTick(() => {
		autosize();
		box.value?.focus();
	});
}

function onKey(e) {
	if (suggestions.value.length) {
		if (e.key === "ArrowDown")
			return (
				e.preventDefault(), (active.value = (active.value + 1) % suggestions.value.length)
			);
		if (e.key === "ArrowUp")
			return (
				e.preventDefault(),
				(active.value =
					(active.value + suggestions.value.length - 1) % suggestions.value.length)
			);
		if (e.key === "Enter" || e.key === "Tab")
			return e.preventDefault(), pick(suggestions.value[active.value]);
		if (e.key === "Escape") return (suggestions.value = []);
	}
	if (e.key === "Escape" && props.replyTo) emit("clear-reply");
	if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
		e.preventDefault();
		submit();
	}
}

async function onFile(e) {
	const picked = e.target.files?.[0];
	e.target.value = "";
	if (!picked) return;
	if (picked.size > 15 * 1024 * 1024) {
		error.value = "The file is larger than 15 MB.";
		return;
	}
	uploading.value = true;
	file.value = { file_name: picked.name };
	try {
		const form = new FormData();
		form.append("file", picked, picked.name);
		form.append("is_private", "1");
		form.append("folder", "Home/Attachments");
		const r = await fetch("/api/method/upload_file", {
			method: "POST",
			body: form,
			headers: { "X-Frappe-CSRF-Token": window.csrf_token },
		});
		const data = await r.json();
		if (!r.ok || !data.message) throw new Error("upload failed");
		file.value = { name: data.message.name, file_name: data.message.file_name };
	} catch (err) {
		file.value = null;
		error.value = "The file could not be uploaded.";
	} finally {
		uploading.value = false;
	}
}

async function submit() {
	if (sending.value || uploading.value) return;
	const body = text.value.trim();
	if (!body && !file.value) return;
	sending.value = true;
	error.value = "";
	try {
		if (mode.value === "note") {
			const note = await call("periskope_connector.api.compose.add_comment", {
				chat: props.chat.name,
				content: body,
				mentions: mentions.value.filter((m) => body.includes("@")),
			});
			emit("noted", note);
		} else {
			const message = await call("periskope_connector.api.compose.send", {
				chat: props.chat.name,
				text: body,
				reply_to: props.replyTo?.name || null,
				file: file.value?.name || null,
			});
			emit("sent", message);
			if (message.send_status === "Not Sent" || message.send_status === "Failed")
				error.value = message.send_error;
		}
		text.value = "";
		file.value = null;
		mentions.value = [];
		emit("clear-reply");
		nextTick(autosize);
	} catch (err) {
		error.value = err?.messages?.[0] || err?.message || "Not sent.";
	} finally {
		sending.value = false;
	}
}

function openSave() {
	newQuick.value = { title: "", body: text.value.trim(), is_shared: false };
	saveDialog.value = true;
}

async function saveQuick() {
	const saved = await call("periskope_connector.api.compose.save_canned_response", {
		title: newQuick.value.title,
		body: newQuick.value.body,
		is_shared: newQuick.value.is_shared ? 1 : 0,
	});
	quick.value = [...quick.value, saved].sort((a, b) => a.title.localeCompare(b.title));
	saveDialog.value = false;
}

async function loadLists() {
	quick.value = await call("periskope_connector.api.compose.canned_responses");
	people.value = await call("periskope_connector.api.compose.mentionable", {
		chat: props.chat.name,
	});
}

watch(
	() => props.replyTo,
	(r) => r && nextTick(() => box.value?.focus())
);
onMounted(loadLists);

defineExpose({ focus: () => box.value?.focus() });
</script>
