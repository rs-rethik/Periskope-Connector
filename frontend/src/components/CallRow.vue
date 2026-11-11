<template>
	<div class="flex justify-center px-4 py-1">
		<div
			class="w-full max-w-[80%] rounded-lg border bg-surface-base px-3 py-2 shadow-sm"
			:class="missed ? 'border-outline-red-2' : 'border-outline-gray-2'"
		>
			<div class="flex items-center gap-2 text-sm text-ink-gray-8">
				<span
					class="grid size-7 shrink-0 place-items-center rounded-full"
					:class="
						missed
							? 'bg-surface-red-2 text-ink-red-7'
							: 'bg-surface-gray-2 text-ink-gray-7'
					"
				>
					<component :is="icon" class="size-3.5" />
				</span>
				<div class="min-w-0 flex-1">
					<div
						class="font-medium"
						:class="missed ? 'text-ink-red-7' : 'text-ink-gray-8'"
					>
						{{ title }}
					</div>
					<div class="truncate text-xs text-ink-gray-5">{{ detail }}</div>
				</div>
				<span class="shrink-0 text-xs text-ink-gray-5">{{
					messageTime(call.call_time)
				}}</span>
			</div>

			<p v-if="call.summary" class="mt-2 whitespace-pre-wrap text-sm text-ink-gray-8">
				{{ call.summary }}
			</p>
			<p
				v-else-if="call.transcript_status === 'pending'"
				class="mt-2 text-xs italic text-ink-gray-5"
			>
				The summary and transcript are on their way.
			</p>

			<div v-if="call.has_recording || call.transcript" class="mt-2 flex flex-wrap gap-2">
				<Button
					v-if="call.has_recording && !audio"
					size="sm"
					label="Play recording"
					:loading="loadingAudio"
					@click="play"
				>
					<template #prefix><LucidePlay class="size-3.5" /></template>
				</Button>
				<Button
					v-if="call.transcript"
					size="sm"
					variant="ghost"
					:label="showTranscript ? 'Hide transcript' : 'Transcript'"
					@click="showTranscript = !showTranscript"
				>
					<template #prefix><LucideFileText class="size-3.5" /></template>
				</Button>
			</div>
			<audio v-if="audio" :src="audio" controls autoplay class="mt-2 h-9 w-full" />
			<ErrorMessage class="mt-1" :message="error" />
			<div
				v-if="showTranscript"
				class="mt-2 max-h-64 overflow-y-auto rounded bg-surface-gray-1 p-2 text-xs leading-relaxed text-ink-gray-8"
			>
				<div v-for="(line, i) in lines" :key="i">
					<b v-if="line.speaker">{{ line.speaker }}:</b> {{ line.text }}
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import { Button, ErrorMessage, call as api } from "frappe-ui";
import LucidePhoneIncoming from "~icons/lucide/phone-incoming";
import LucidePhoneOutgoing from "~icons/lucide/phone-outgoing";
import LucidePhoneMissed from "~icons/lucide/phone-missed";
import LucidePlay from "~icons/lucide/play";
import LucideFileText from "~icons/lucide/file-text";
import { messageTime } from "@/utils/format";
import { errorText } from "@/utils/errors";

// A WhatsApp call in the conversation (calls.py): who called, how it ended, how long, who took it;
// for calls taken in Periskope, its summary, transcript and recording (api/calls.py).
const props = defineProps({ call: { type: Object, required: true } });

const NOT_ANSWERED = ["missed", "rejected", "unavailable", "cancelled", "abandoned"];
const RESULT = {
	missed: "missed",
	rejected: "declined",
	unavailable: "not answered",
	cancelled: "cancelled",
	abandoned: "cancelled",
	failed: "failed",
	invalid: "failed",
	ongoing: "in progress",
	upcoming: "ringing",
	acceptedelsewhere: "answered on another device",
};

const incoming = computed(() => props.call.direction === "Incoming");
const missed = computed(() => incoming.value && NOT_ANSWERED.includes(props.call.result));
const icon = computed(() =>
	missed.value ? LucidePhoneMissed : incoming.value ? LucidePhoneIncoming : LucidePhoneOutgoing
);
const title = computed(() => {
	if (missed.value) return "Missed call";
	const kind = incoming.value ? "Incoming call" : "Outgoing call";
	const r = RESULT[props.call.result];
	return r && props.call.result !== "connected" ? `${kind} · ${r}` : kind;
});

function length(seconds) {
	const s = Math.round(seconds || 0);
	return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const detail = computed(() => {
	const c = props.call;
	const parts = [];
	if (c.duration) parts.push(length(c.duration));
	if (c.answered_by_name)
		parts.push(`${incoming.value ? "answered by" : "by"} ${c.answered_by_name}`);
	else if (c.handled_via === "phone" && c.result === "connected") parts.push("on the phone");
	return parts.join(" · ");
});

const lines = computed(() =>
	(props.call.transcript || "").split("\n").map((l) => {
		const i = l.indexOf(": ");
		return i > 0 ? { speaker: l.slice(0, i), text: l.slice(i + 2) } : { speaker: "", text: l };
	})
);

const showTranscript = ref(false);
const audio = ref(null);
const loadingAudio = ref(false);
const error = ref("");

async function play() {
	loadingAudio.value = true;
	error.value = "";
	try {
		audio.value = (
			await api("periskope_connector.api.calls.recording", { call: props.call.name })
		).url;
	} catch (e) {
		error.value = errorText(e, "The recording could not be loaded.");
	} finally {
		loadingAudio.value = false;
	}
}
</script>
