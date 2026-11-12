<template>
	<Dialog v-model="open" :options="{ title: 'What the AI decided', size: 'md' }">
		<template #body-content>
			<div v-if="!d" class="flex justify-center p-4">
				<LoadingIndicator class="size-5" />
			</div>
			<div v-else class="flex flex-col gap-3 text-sm">
				<dl class="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5">
					<dt class="text-ink-gray-5">Status</dt>
					<dd class="text-ink-gray-8">
						{{ d.classification_status || "Not analysed" }}
						<span v-if="d.classification_error" class="block text-xs text-ink-red-7">{{
							d.classification_error
						}}</span>
					</dd>
					<dt class="text-ink-gray-5">Tag</dt>
					<dd class="text-ink-gray-8">
						{{ d.ai_category || "—" }}
						<span v-if="d.correct_category" class="text-ink-green-7">
							→ {{ d.correct_category }} (corrected)</span
						>
					</dd>
					<dt class="text-ink-gray-5">Severity · mood</dt>
					<dd class="text-ink-gray-8">
						{{ d.severity || "—" }} · {{ d.sentiment || "—" }}
					</dd>
					<dt class="text-ink-gray-5">Sure</dt>
					<dd class="text-ink-gray-8">
						{{ d.ai_confidence ? d.ai_confidence + "%" : "—" }}
					</dd>
					<dt class="text-ink-gray-5">Needs a reply</dt>
					<dd class="text-ink-gray-8">{{ d.needs_reply ? "Yes" : "No" }}</dd>
					<dt class="text-ink-gray-5">Escalated</dt>
					<dd class="text-ink-gray-8">
						{{ d.escalation || "No" }}
						<span v-if="d.escalation_reason" class="block text-xs text-ink-gray-6">{{
							d.escalation_reason
						}}</span>
						<span
							v-if="d.escalation_feedback"
							class="block text-xs"
							:class="
								d.escalation_feedback === 'Wrong'
									? 'text-ink-red-7'
									: 'text-ink-green-7'
							"
							>A manager marked this {{ d.escalation_feedback.toLowerCase() }}</span
						>
					</dd>
					<dt class="text-ink-gray-5">Summary</dt>
					<dd class="text-ink-gray-8">{{ d.ai_summary || "—" }}</dd>
					<template v-if="d.ai_lead">
						<dt class="text-ink-gray-5">Lead made</dt>
						<dd class="text-ink-gray-8">
							{{ d.ai_lead }}{{ d.lead_exists ? "" : " (removed)" }}
						</dd>
					</template>
				</dl>

				<!-- managers correct it; the learning agent reads the corrections -->
				<div
					v-if="d.can_correct"
					class="flex flex-col gap-2 rounded-lg border border-outline-gray-1 p-3"
				>
					<FormControl
						v-model="tag"
						type="select"
						label="The right tag"
						:options="[
							{ label: 'Keep the AI\'s tag', value: '' },
							...d.tags.map((t) => ({ label: t, value: t })),
						]"
					/>
					<FormControl
						v-model="note"
						type="textarea"
						:rows="2"
						label="Note for the team (optional)"
					/>
					<div class="flex flex-wrap gap-2">
						<Button
							size="sm"
							label="Save the tag"
							:disabled="!tag && !note.trim()"
							:loading="busy === 'tag'"
							@click="correct({ category: tag })"
						/>
						<Button
							size="sm"
							:label="
								d.escalation ? 'Escalation was wrong' : 'Should have escalated'
							"
							:loading="busy === 'esc'"
							@click="correct({ escalation: 'Wrong' }, 'esc')"
						/>
						<Button
							v-if="d.escalation"
							size="sm"
							label="Escalation was right"
							:loading="busy === 'ok'"
							@click="correct({ escalation: 'Right' }, 'ok')"
						/>
						<Button
							size="sm"
							label="Analyse again"
							:loading="busy === 'again'"
							@click="again"
						/>
						<Button
							v-if="d.ai_lead && d.lead_exists"
							size="sm"
							theme="red"
							variant="subtle"
							label="Remove the lead the AI made"
							:loading="busy === 'lead'"
							@click="removeLead"
						/>
					</div>
					<ErrorMessage :message="error" />
				</div>
			</div>
		</template>
	</Dialog>
</template>

<script setup>
import { ref, watch } from "vue";
import {
	Button,
	Dialog,
	ErrorMessage,
	FormControl,
	LoadingIndicator,
	call,
	toast,
} from "frappe-ui";
import { ask } from "@/utils/confirm";
import { errorText } from "@/utils/errors";

// What the AI made of one customer message (api/ai_review.py), and the managers' corrections.
const props = defineProps({ message: { type: String, required: true } });
const open = defineModel({ type: Boolean, default: false });
const API = "periskope_connector.api.ai_review.";
const d = ref(null);
const tag = ref("");
const note = ref("");
const busy = ref("");
const error = ref("");

async function load() {
	d.value = null;
	error.value = "";
	try {
		d.value = await call(API + "details", { message: props.message });
		tag.value = d.value.correct_category || "";
	} catch (e) {
		error.value = errorText(e, "Could not read what the AI decided.");
		open.value = false;
	}
}
watch(open, (o) => o && load(), { immediate: true });

async function correct(args, key = "tag") {
	busy.value = key;
	error.value = "";
	try {
		d.value = {
			...d.value,
			...(await call(API + "correct", {
				message: props.message,
				note: note.value,
				...args,
			})),
		};
		note.value = "";
		toast.success("Saved: the learning agent will take it into account");
	} catch (e) {
		error.value = errorText(e, "Not saved.");
	} finally {
		busy.value = "";
	}
}

async function again() {
	busy.value = "again";
	try {
		await call(API + "analyse_again", { message: props.message });
		toast.success("Sent to the AI again");
		open.value = false;
	} catch (e) {
		error.value = errorText(e, "Could not send it again.");
	} finally {
		busy.value = "";
	}
}

async function removeLead() {
	const ok = await ask(
		`Lead ${d.value.ai_lead} is deleted with the notes and tasks the AI wrote on it, and this number goes back to the contact queue. Only for a number that is not a customer.`,
		{ title: "Remove the lead?", confirmLabel: "Remove", theme: "red" }
	);
	if (!ok) return;
	busy.value = "lead";
	try {
		await call(API + "remove_ai_lead", { message: props.message, note: note.value });
		toast.success("Lead removed");
		load();
	} catch (e) {
		error.value = errorText(e, "Could not remove the lead.");
	} finally {
		busy.value = "";
	}
}
</script>
