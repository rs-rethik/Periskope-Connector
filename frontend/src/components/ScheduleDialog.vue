<template>
	<Dialog
		v-model="open"
		:options="{ title: editing ? 'Edit scheduled message' : 'Send later', size: 'lg' }"
	>
		<template #body-content>
			<div class="flex flex-col gap-4">
				<FormControl
					v-model="form.text"
					type="textarea"
					label="Message"
					:rows="4"
					placeholder="Type the message to send"
				/>
				<div
					v-if="fileName"
					class="flex items-center gap-2 rounded-lg bg-surface-gray-1 px-2 py-1.5 text-sm text-ink-gray-7"
				>
					<LucidePaperclip class="size-4" /> {{ fileName }}
				</div>

				<div class="flex flex-col gap-1.5">
					<span class="text-xs text-ink-gray-5">Send at</span>
					<DateTimePicker v-model="form.when" placeholder="Pick a date and time" />
					<div class="flex flex-wrap gap-1">
						<button
							v-for="q in quick"
							:key="q.label"
							class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-7 hover:bg-surface-gray-3"
							@click="form.when = q.value()"
						>
							{{ q.label }}
						</button>
					</div>
				</div>

				<div
					v-if="canRepeat"
					class="flex flex-col gap-3 rounded-xl border border-outline-gray-2 p-3"
				>
					<FormControl
						v-model="form.repeats"
						type="checkbox"
						label="Repeat this message"
					/>
					<template v-if="form.repeats">
						<div class="flex items-end gap-2">
							<FormControl
								v-model="form.value"
								type="number"
								label="Every"
								class="w-20"
								:min="1"
							/>
							<FormControl
								v-model="form.interval"
								type="select"
								class="w-32"
								:options="[
									{ label: form.value > 1 ? 'days' : 'day', value: 'day' },
									{ label: form.value > 1 ? 'weeks' : 'week', value: 'week' },
									{ label: form.value > 1 ? 'months' : 'month', value: 'month' },
								]"
							/>
						</div>
						<div v-if="form.interval === 'day'" class="flex flex-col gap-1">
							<span class="text-xs text-ink-gray-5">Only on</span>
							<div class="flex flex-wrap gap-1">
								<button
									v-for="[d, short] in WEEKDAYS"
									:key="d"
									class="rounded-full px-2.5 py-1 text-xs font-medium"
									:class="
										form.days.includes(d)
											? 'bg-surface-gray-7 text-ink-white'
											: 'bg-surface-gray-2 text-ink-gray-7'
									"
									@click="toggleDay(d)"
								>
									{{ short }}
								</button>
							</div>
						</div>
						<div class="flex flex-col gap-1">
							<span class="text-xs text-ink-gray-5">Ends on (optional)</span>
							<DatePicker v-model="form.ends" placeholder="No end date" />
						</div>
						<p
							class="rounded-lg bg-surface-amber-1 px-2 py-1.5 text-xs text-ink-amber-8"
						>
							A repeating message goes to the customer every time. Keep it to real
							reminders, so they do not see it as spam.
						</p>
					</template>
				</div>

				<p v-if="summary" class="text-sm text-ink-gray-7">{{ summary }}</p>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				:label="editing ? 'Save' : 'Schedule'"
				:loading="saving"
				:disabled="!form.when || (!form.text.trim() && !fileName)"
				@click="save"
			>
				<template #prefix><LucideClock class="size-4" /></template>
			</Button>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import dayjs from "dayjs";
import {
	Button,
	DatePicker,
	DateTimePicker,
	Dialog,
	ErrorMessage,
	FormControl,
	call,
} from "frappe-ui";
import LucideClock from "~icons/lucide/clock";
import LucidePaperclip from "~icons/lucide/paperclip";
import { WEEKDAYS, repeatText, when } from "@/utils/schedule";
import { errorText } from "@/utils/errors";

// Schedule a message in a chat (api/scheduled.py), or edit a scheduled one. Periskope sends it.
// Repeating is offered to managers only.
const open = defineModel({ type: Boolean, default: false });
const props = defineProps({
	chat: { type: String, required: true },
	text: { type: String, default: "" }, // what was typed in the reply box
	file: { type: Object, default: null }, // its attachment ({ name, file_name })
	editing: { type: Object, default: null }, // a scheduled row to edit
	canRepeat: { type: Boolean, default: false },
});
const emit = defineEmits(["saved"]);

const PICKED = "YYYY-MM-DD HH:mm:ss";
const at = (days, hour) => () =>
	dayjs().add(days, "day").hour(hour).minute(0).second(0).format(PICKED);
const nextMonday = () =>
	dayjs()
		.add((8 - dayjs().day()) % 7 || 7, "day")
		.hour(10)
		.minute(0)
		.second(0)
		.format(PICKED);
const quick = [
	{ label: "In 1 hour", value: () => dayjs().add(1, "hour").second(0).format(PICKED) },
	{ label: "Tomorrow 10 AM", value: at(1, 10) },
	{ label: "Tomorrow 5 PM", value: at(1, 17) },
	{ label: "Monday 10 AM", value: nextMonday },
];

const blank = () => ({
	text: "",
	when: "",
	repeats: false,
	interval: "week",
	value: 1,
	days: [],
	ends: "",
});
const form = ref(blank());
const error = ref("");
const saving = ref(false);
const fileName = computed(() =>
	props.editing ? props.editing.file?.split("/").pop() : props.file?.file_name
);

watch(open, (isOpen) => {
	if (!isOpen) return;
	error.value = "";
	const r = props.editing;
	form.value = r
		? {
				text: r.message || "",
				when: r.next_run ? dayjs(r.next_run).format(PICKED) : "",
				repeats: !!r.is_repeat,
				interval: r.repeat_interval || "week",
				value: r.repeat_value || 1,
				days: [...(r.repeat_days || [])],
				ends: r.repeat_ends ? dayjs(r.repeat_ends).format("YYYY-MM-DD") : "",
		  }
		: { ...blank(), text: props.text || "" };
});

function toggleDay(d) {
	const days = form.value.days;
	form.value.days = days.includes(d) ? days.filter((x) => x !== d) : [...days, d];
}

const repeat = computed(() =>
	form.value.repeats
		? {
				interval: form.value.interval,
				value: Number(form.value.value) || 1,
				days: form.value.interval === "day" ? form.value.days : [],
				ends: form.value.ends ? `${form.value.ends} 23:59:00` : null,
		  }
		: null
);

const summary = computed(() => {
	if (!form.value.when) return "";
	const first = when(form.value.when);
	return repeat.value
		? `First on ${first}, then ${repeatText(repeat.value).toLowerCase()}.`
		: `Sends on ${first}.`;
});

async function save() {
	saving.value = true;
	error.value = "";
	try {
		const args = { text: form.value.text, when: form.value.when };
		if (props.canRepeat) args.repeat = repeat.value ? JSON.stringify(repeat.value) : "";
		const row = props.editing
			? await call("periskope_connector.api.scheduled.update", {
					name: props.editing.name,
					...args,
			  })
			: await call("periskope_connector.api.scheduled.schedule", {
					chat: props.chat,
					...args,
					file: props.file?.name || null,
			  });
		emit("saved", row);
		open.value = false;
	} catch (e) {
		error.value = errorText(e, "The message could not be scheduled.");
	} finally {
		saving.value = false;
	}
}
</script>
