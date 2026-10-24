<template>
	<div class="flex h-full min-h-0 flex-col">
		<!-- title and its buttons on one line, the description below at full width -->
		<div class="border-b border-outline-gray-1 px-6 py-4">
			<div class="flex flex-wrap items-center gap-2">
				<h2 class="mr-auto text-lg font-semibold text-ink-gray-9">{{ section?.title }}</h2>
				<Button
					v-for="a in actions"
					:key="a.action"
					:label="a.label"
					:loading="running === a.action"
					:disabled="!!running && running !== a.action"
					@click="run(a)"
				/>
			</div>
			<p v-if="hint" class="mt-1 text-sm text-ink-gray-5">{{ hint }}</p>
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto px-6 py-4">
			<!-- The setup steps, as on the Settings form -->
			<div v-if="setup && sectionKey === 'connect'" class="mb-5 grid gap-2 sm:grid-cols-2">
				<div
					v-for="step in setup.steps"
					:key="step.key"
					class="rounded-lg border px-3 py-2"
					:class="
						step.ok
							? 'border-outline-green-2 bg-surface-green-1'
							: 'border-outline-amber-2 bg-surface-amber-1'
					"
				>
					<div class="text-sm font-medium text-ink-gray-8">{{ step.title }}</div>
					<div class="text-xs text-ink-gray-6">{{ step.message }}</div>
				</div>
			</div>
			<div
				v-if="sectionKey === 'test_mode' && form.test_mode"
				class="mb-4 rounded-lg bg-surface-red-1 px-3 py-2 text-sm text-ink-red-8"
			>
				Test mode is on: WhatsApp messages go only to the numbers below, and emails only to
				the addresses below.
			</div>
			<div class="grid max-w-2xl gap-4">
				<FieldInput
					v-for="f in visibleFields"
					:key="f.fieldname"
					v-model="form[f.fieldname]"
					:field="f"
				/>
			</div>
		</div>
		<!-- always in view, however long the section -->
		<div
			class="flex items-center gap-3 border-t border-outline-gray-1 bg-surface-base px-6 py-3"
		>
			<Button
				variant="solid"
				label="Save"
				:loading="saving"
				:disabled="!dirty"
				@click="ask"
			/>
			<span v-if="dirty" class="text-xs text-ink-gray-6">Unsaved changes</span>
			<span v-else-if="section?.confirm" class="text-xs text-ink-gray-5"
				>Saving asks you to confirm: this changes the live setup.</span
			>
			<ErrorMessage class="ml-auto" :message="error" />
		</div>

		<Dialog v-model="confirming" :options="{ title: 'Change the live setup?' }">
			<template #body-content>
				<p class="text-sm text-ink-gray-7">
					{{
						sectionKey === "test_mode"
							? "This changes who can receive WhatsApp messages and emails from this site."
							: "This changes how this site connects to Periskope and receives its messages."
					}}
					Continue?
				</p>
			</template>
			<template #actions>
				<Button variant="solid" theme="red" label="Yes, save" @click="save" />
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Button, Dialog, ErrorMessage, call, toast } from "frappe-ui";
import FieldInput from "@/components/settings/FieldInput.vue";

const props = defineProps({
	sectionKey: { type: String, required: true },
	data: { type: Object, default: null },
});
const emit = defineEmits(["saved", "dirty"]);

const form = ref({});
const error = ref("");
const saving = ref(false);
const confirming = ref(false);
const running = ref("");

const section = computed(() => props.data?.sections.find((s) => s.key === props.sectionKey));
const setup = computed(() => props.data?.setup);

// Shown when they apply, as on the Settings form (depends_on).
const visibleFields = computed(() =>
	(section.value?.fields || []).filter((f) => {
		if (["anthropic_api_key", "ai_model", "ai_base_url"].includes(f.fieldname))
			return form.value.ai_provider !== "Jarvis";
		if (f.fieldname === "reply_delay_minutes") return !!form.value.enable_auto_reply;
		if (["test_allowed_chats", "test_allowed_emails"].includes(f.fieldname))
			return !!form.value.test_mode;
		return true;
	})
);

const ACTIONS = {
	connect: [
		{ action: "test_connection", label: "Test connection" },
		{ action: "sync_numbers", label: "Sync numbers" },
		{
			action: "setup_webhooks",
			label: "Create webhook in Periskope",
			confirm:
				"This registers this site's webhook in your live Periskope account. Continue?",
		},
	],
	ai: [{ action: "test_ai", label: "Test AI" }],
};
const actions = computed(() => ACTIONS[props.sectionKey] || []);
const hint = computed(
	() =>
		({
			connect:
				"The Periskope account behind every number. Keys are never shown here once saved.",
			ai: "The model that tags messages, writes replies and learns from chats.",
			team: "Who approves the agent's suggestions and who hears about escalations and SLA breaches.",
			auto_reply:
				"When the AI answers a customer nobody has answered, and the hours the SLA clocks count.",
			test_mode:
				"While testing, messages and emails go only to the listed numbers and addresses.",
			voice: "Google Gemini turns customers' voice notes into text.",
		}[props.sectionKey] || "")
);

// The values as the server has them; a field differs from it once the user edits it.
function stored(s) {
	const values = {};
	for (const f of s?.fields || [])
		values[f.fieldname] = f.fieldtype === "Password" ? "" : f.value;
	return values;
}

// New settings from the server (after a save, a test or a sync) refresh the fields the user has not
// touched; what they typed and have not saved yet stays.
watch(
	section,
	(s, old) => {
		const next = stored(s);
		const before = old && old.key === s?.key ? stored(old) : null;
		if (before)
			for (const k of Object.keys(next))
				if (form.value[k] !== before[k]) next[k] = form.value[k];
		form.value = next;
		if (!before) error.value = "";
	},
	{ immediate: true }
);

const dirty = computed(() => JSON.stringify(form.value) !== JSON.stringify(stored(section.value)));
watch(dirty, (d) => emit("dirty", d));
onBeforeUnmount(() => emit("dirty", false));

function ask() {
	if (section.value?.confirm) confirming.value = true;
	else save();
}

async function save() {
	if (saving.value) return;
	confirming.value = false;
	saving.value = true;
	error.value = "";
	try {
		const values = {};
		for (const f of section.value.fields)
			if (!f.read_only) values[f.fieldname] = form.value[f.fieldname];
		const data = await call("periskope_connector.api.admin.save_settings", {
			section: props.sectionKey,
			values,
		});
		toast.success("Saved");
		emit("saved", data);
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "Not saved.";
	} finally {
		saving.value = false;
	}
}

async function run(a) {
	if (running.value || (a.confirm && !window.confirm(a.confirm))) return;
	running.value = a.action;
	error.value = "";
	try {
		const r = await call("periskope_connector.api.admin.run_action", { action: a.action });
		toast.success(`${a.label}: done`);
		emit("saved", null, r);
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || `${a.label} failed.`;
		emit("saved");
	} finally {
		running.value = "";
	}
}
</script>
