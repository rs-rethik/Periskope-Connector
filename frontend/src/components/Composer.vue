<template>
	<div class="safe-bottom border-t border-outline-gray-1 bg-surface-base">
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
						: `Sends from ${chat.number_label}${isMeta ? " (Meta)" : ""}`
				}}
			</span>
		</div>

		<!-- Meta WhatsApp: free text only within 24 hours of the customer's last message -->
		<div
			v-if="isMeta && mode === 'reply'"
			class="mx-3 mt-2 flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs"
			:class="
				windowOpen
					? 'bg-surface-gray-1 text-ink-gray-6'
					: 'bg-surface-amber-1 text-ink-amber-8'
			"
		>
			<LucideClock class="size-3.5 shrink-0" />
			<span class="min-w-0 flex-1 truncate">{{ windowText }}</span>
			<Button size="sm" variant="subtle" label="Send a template" @click="templates = true">
				<template #prefix><LucideFileText class="size-3.5" /></template>
			</Button>
		</div>
		<TemplateDialog
			v-if="isMeta"
			v-model="templates"
			:chat="chat"
			@sent="(m) => emit('sent', m)"
		/>

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
			<Tooltip text="Cancel reply"
				><button
					class="text-ink-gray-5 hover:text-ink-gray-8"
					aria-label="Cancel reply"
					@click="$emit('clear-reply')"
				>
					<LucideX class="size-4" /></button
			></Tooltip>
		</div>

		<div
			v-if="file && mode === 'reply'"
			class="mx-3 mt-2 flex items-center gap-2 rounded bg-surface-gray-1 px-2 py-1 text-sm"
		>
			<LucidePaperclip class="size-4 text-ink-gray-6" />
			<span class="truncate text-ink-gray-8">{{ file.file_name }}</span>
			<span v-if="uploading" class="text-xs text-ink-gray-5">uploading…</span>
			<Tooltip text="Remove file"
				><button
					class="ml-auto text-ink-gray-5 hover:text-ink-gray-8"
					aria-label="Remove file"
					@click="removeFile"
				>
					<LucideX class="size-4" /></button
			></Tooltip>
		</div>

		<div
			v-if="mode === 'reply' && quick.length"
			class="flex gap-1.5 overflow-x-auto px-3 pt-2 [scrollbar-width:none]"
		>
			<Tooltip v-for="q in quick.slice(0, 6)" :key="q.name || q.title" :text="q.body"
				><button
					class="flex shrink-0 items-center gap-1 rounded-full border border-outline-gray-2 bg-surface-base px-2.5 py-1 text-sm text-ink-gray-7 transition hover:border-outline-gray-3 hover:bg-surface-gray-1"
					:aria-label="q.body"
					@click="insert(q.body, true)"
				>
					<LucideZap class="size-3 text-ink-amber-6" />{{ q.title }}
				</button></Tooltip
			>
		</div>
		<div class="relative px-3 pb-3 pt-2">
			<div
				v-if="suggestions.length"
				class="absolute bottom-full left-3 z-10 mb-1 max-h-60 w-80 overflow-y-auto rounded-lg border border-outline-gray-2 bg-surface-base p-1 shadow-lg"
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
			<div
				class="rounded-2xl border border-outline-gray-2 px-2 pb-1.5 pt-1 transition focus-within:border-outline-gray-3 focus-within:shadow-sm"
				:class="mode === 'note' ? 'bg-surface-amber-1' : 'bg-surface-gray-1'"
			>
				<textarea
					ref="box"
					v-model="text"
					rows="1"
					class="max-h-48 min-h-[40px] w-full resize-none rounded border-0 bg-transparent p-1 text-base text-ink-gray-9 placeholder-ink-gray-4 focus:ring-0 max-md:text-[16px]"
					:placeholder="
						mode === 'note'
							? 'Write a note for your team…'
							: locked
							? 'The 24-hour window is closed. Send a template to reach the customer.'
							: 'Type a message. / for quick replies'
					"
					:disabled="locked"
					@input="onInput"
					@keydown="onKey"
				/>
				<div class="flex items-center gap-1">
					<Popover v-if="mode === 'reply'">
						<template #target="{ togglePopover }">
							<Button
								variant="ghost"
								tooltip="Emoji"
								aria-label="Emoji"
								@click="togglePopover()"
								><template #icon><LucideSmile class="size-4" /></template
							></Button>
						</template>
						<template #body="{ close }">
							<div
								class="grid w-72 grid-cols-8 gap-1 rounded-lg border border-outline-gray-2 bg-surface-base p-2 shadow-lg"
							>
								<button
									v-for="e in EMOJI"
									:key="e"
									class="rounded p-1 text-lg hover:bg-surface-gray-2"
									:aria-label="`Insert ${e}`"
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
						tooltip="Attach a file"
						aria-label="Attach a file"
						@click="$refs.picker.click()"
					>
						<template #icon><LucidePaperclip class="size-4" /></template>
					</Button>
					<input ref="picker" type="file" class="hidden" @change="onFile" />
					<Button
						v-if="mode === 'reply' && !isMeta"
						variant="ghost"
						tooltip="Send later"
						aria-label="Send later"
						:disabled="uploading"
						@click="scheduling = true"
					>
						<template #icon><LucideClock class="size-4" /></template>
					</Button>
					<Dropdown v-if="mode === 'reply'" :options="quickOptions">
						<Button variant="ghost" tooltip="Quick replies" aria-label="Quick replies"
							><template #icon><LucideZap class="size-4" /></template
						></Button>
					</Dropdown>
					<span v-if="error" class="ml-2 truncate text-sm text-ink-red-8">{{
						error
					}}</span>
					<Button
						class="ml-auto !rounded-full"
						variant="solid"
						:label="mode === 'note' ? 'Add note' : 'Send'"
						:loading="sending"
						:disabled="!canSubmit"
						@click="submit"
					>
						<template #suffix><LucideSendHorizontal class="size-4" /></template>
					</Button>
				</div>
			</div>
		</div>

		<ScheduleDialog
			v-model="scheduling"
			:chat="chat.name"
			:text="text"
			:file="file?.name ? file : null"
			:can-repeat="!!boot.is_manager"
			@saved="onScheduled"
		/>

		<Dialog v-model="saveDialog" :options="{ title: 'Save as quick reply' }">
			<template #body-content>
				<form class="flex flex-col gap-3" @submit.prevent="saveQuick">
					<FormControl v-model="newQuick.title" label="Title" autofocus />
					<FormControl v-model="newQuick.body" type="textarea" label="Message" />
					<FormControl
						v-if="boot.is_manager"
						v-model="newQuick.is_shared"
						type="checkbox"
						label="Share with everyone"
					/>
					<button type="submit" class="hidden" />
				</form>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Save"
					:loading="savingQuick"
					:disabled="!newQuick.title || !newQuick.body"
					@click="saveQuick"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import dayjs from "dayjs";
import { Button, Dialog, Dropdown, FormControl, Popover, Tooltip, call, toast } from "frappe-ui";
import LucideX from "~icons/lucide/x";
import LucidePaperclip from "~icons/lucide/paperclip";
import ScheduleDialog from "@/components/ScheduleDialog.vue";
import LucideSmile from "~icons/lucide/smile";
import LucideZap from "~icons/lucide/zap";
import LucideSendHorizontal from "~icons/lucide/send-horizontal";
import LucideClock from "~icons/lucide/clock";
import LucideFileText from "~icons/lucide/file-text";
import TemplateDialog from "@/components/TemplateDialog.vue";
import { MAX_UPLOAD_BYTES, uploadPrivate } from "@/utils/upload";
import { boot } from "@/boot";
import { drafts, saveDraft } from "@/stores/inbox";
import { errorText, showError } from "@/utils/errors";
import { isMobile } from "@/utils/screen";

const props = defineProps({
	chat: { type: Object, required: true },
	replyTo: { type: Object, default: null },
});
const emit = defineEmits(["sent", "noted", "clear-reply", "scheduled"]);

// A Meta WhatsApp chat (meta.py): free text only within 24 hours of the customer's last message,
// an approved template any time.
const isMeta = computed(() => props.chat.source === "Meta");
const windowOpen = computed(() => !!props.chat.window?.open);
const locked = computed(() => isMeta.value && !windowOpen.value && mode.value === "reply");
const templates = ref(false);
const windowText = computed(() => {
	if (!windowOpen.value)
		return props.chat.window?.closes_at
			? "Window closed: templates only"
			: "The customer has not written yet: templates only";
	return `Free reply until ${dayjs(props.chat.window.closes_at).format("D MMM, h:mm A")}`;
});

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
const scheduling = ref(false); // the "Send later" dialog (ScheduleDialog)
function onScheduled(row) {
	text.value = "";
	file.value = null;
	emit("scheduled", row);
	nextTick(autosize);
}
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
const savingQuick = ref(false);
let uploadId = 0; // a file removed while it uploads must not come back when the upload ends

// A note needs text (a file cannot go in a note); a reply needs text or a file.
const canSubmit = computed(
	() =>
		!uploading.value &&
		!sending.value &&
		!locked.value &&
		(mode.value === "note" ? !!text.value.trim() : !!text.value.trim() || !!file.value)
);

// Each chat keeps its own unsent text, file and mode, so switching chats neither loses a draft nor
// carries it into the other chat.
const saved = drafts.get(props.chat.name);
if (saved) {
	mode.value = saved.mode;
	text.value = saved.text;
	file.value = saved.file;
	mentions.value = saved.mentions;
}
function keepDraft() {
	saveDraft(
		props.chat.name,
		text.value || file.value
			? {
					mode: mode.value,
					text: text.value,
					file: file.value?.name ? file.value : null,
					mentions: [...mentions.value],
			  }
			: null
	);
}
watch([mode, text, file, mentions], keepDraft, { deep: true });
let left = false; // another chat was opened: this composer is gone, its uploads may still end
onBeforeUnmount(() => (left = true));

function removeFile() {
	uploadId++;
	file.value = null;
	uploading.value = false;
}
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

// Put text where the cursor is (a quick reply replaces an empty box or a "/" search).
function insert(value, replace = false) {
	const el = box.value;
	let caret;
	if (replace && (!text.value.trim() || text.value.trim().startsWith("/"))) {
		text.value = value;
		caret = value.length;
	} else {
		const at = el && document.activeElement === el ? el.selectionStart : text.value.length;
		const before = text.value.slice(0, at);
		const pad = before && !before.endsWith(" ") ? " " : "";
		text.value = before + pad + value + text.value.slice(at);
		caret = at + pad.length + value.length;
	}
	nextTick(() => {
		autosize();
		box.value?.focus();
		box.value?.setSelectionRange(caret, caret);
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
		if (!mentions.value.some((m) => m.name === s.value.name))
			mentions.value.push({ name: s.value.name, label: s.label });
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
		if (e.key === "Escape") return e.preventDefault(), (suggestions.value = []);
	}
	if (e.key === "Escape" && props.replyTo) return e.preventDefault(), emit("clear-reply");
	// a phone's Enter is a new line (its keyboard has no Shift+Enter): the Send button sends
	if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !isMobile.value) {
		e.preventDefault();
		submit();
	}
}

async function onFile(e) {
	const picked = e.target.files?.[0];
	e.target.value = "";
	if (!picked) return;
	if (picked.size > MAX_UPLOAD_BYTES) {
		error.value = "The file is larger than 15 MB.";
		return;
	}
	const id = ++uploadId;
	uploading.value = true;
	file.value = { file_name: picked.name };
	try {
		const saved = await uploadPrivate(picked);
		if (id !== uploadId) return;
		file.value = saved;
		if (left) {
			// the watcher stopped with the composer: put the file in the chat's draft and say so
			keepDraft();
			toast.info(
				`${saved.file_name} is uploaded and waits in the draft of ${props.chat.title}.`
			);
		}
	} catch (err) {
		if (id !== uploadId) return;
		file.value = null;
		error.value = "The file could not be uploaded.";
		if (left) showError(null, `The file for ${props.chat.title} could not be uploaded.`);
	} finally {
		if (id === uploadId) uploading.value = false;
	}
}

async function submit() {
	if (!canSubmit.value) return;
	const body = text.value.trim();
	sending.value = true;
	error.value = "";
	try {
		if (mode.value === "note") {
			const note = await call("periskope_connector.api.compose.add_comment", {
				chat: props.chat.name,
				content: body,
				// only those still named in the text
				mentions: mentions.value
					.filter((m) => body.includes("@" + m.label))
					.map((m) => m.name),
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
		// at once, not by the watcher: it no longer runs when the chat was left while this was sent
		saveDraft(props.chat.name);
		emit("clear-reply");
		nextTick(autosize);
	} catch (err) {
		error.value = errorText(err, "Not sent.");
	} finally {
		sending.value = false;
	}
}

function openSave() {
	newQuick.value = { title: "", body: text.value.trim(), is_shared: false };
	saveDialog.value = true;
}

async function saveQuick() {
	if (savingQuick.value || !newQuick.value.title || !newQuick.value.body) return;
	savingQuick.value = true;
	try {
		const row = await call("periskope_connector.api.compose.save_canned_response", {
			title: newQuick.value.title,
			body: newQuick.value.body,
			is_shared: newQuick.value.is_shared ? 1 : 0,
		});
		quick.value = [...quick.value, row].sort((a, b) => a.title.localeCompare(b.title));
		saveDialog.value = false;
	} catch (e) {
		showError(e, "The quick reply was not saved.");
	} finally {
		savingQuick.value = false;
	}
}

// Quick replies and the people a note can mention; either may fail without blocking the other.
async function loadLists() {
	const [q, p] = await Promise.allSettled([
		call("periskope_connector.api.compose.canned_responses"),
		call("periskope_connector.api.compose.mentionable", { chat: props.chat.name }),
	]);
	if (q.status === "fulfilled") quick.value = q.value;
	if (p.status === "fulfilled") people.value = p.value;
}

watch(
	() => props.replyTo,
	(r) => r && nextTick(() => box.value?.focus())
);
onMounted(() => {
	loadLists();
	nextTick(autosize);
});

defineExpose({ focus: () => box.value?.focus() });
</script>
