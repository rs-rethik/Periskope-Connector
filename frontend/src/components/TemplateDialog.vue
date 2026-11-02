<template>
	<Dialog v-model="show" :options="{ title: 'Send a template', size: 'xl' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<p class="text-sm text-ink-gray-6">
					Meta takes a free message only within 24 hours of the customer's last message.
					An approved template can be sent any time.
				</p>
				<div v-if="loading" class="flex justify-center py-6">
					<LoadingIndicator class="size-5" />
				</div>
				<template v-else>
					<TextInput
						v-model="search"
						placeholder="Search templates"
						:debounce="150"
						class="search-pill"
					>
						<template #prefix
							><LucideSearch class="size-4 text-ink-gray-5"
						/></template>
					</TextInput>
					<div
						class="max-h-56 overflow-y-auto rounded-xl border border-outline-gray-1 p-1"
					>
						<button
							v-for="t in shown"
							:key="t.name"
							class="flex w-full flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left transition hover:bg-surface-gray-1"
							:class="picked?.name === t.name ? 'bg-surface-gray-2' : ''"
							@click="pick(t)"
						>
							<span class="flex w-full items-center gap-2">
								<span class="truncate text-sm font-medium text-ink-gray-9">{{
									t.label
								}}</span>
								<span
									class="ml-auto shrink-0 rounded-full bg-surface-gray-2 px-2 text-xs text-ink-gray-6"
									>{{ t.category?.toLowerCase() }}</span
								>
							</span>
							<span class="line-clamp-1 text-xs text-ink-gray-5">{{ t.body }}</span>
						</button>
						<div v-if="!shown.length" class="p-4 text-center text-sm text-ink-gray-5">
							{{
								templates.length
									? "No template matches."
									: "No approved template without a header file."
							}}
						</div>
					</div>

					<template v-if="picked">
						<div v-if="picked.params" class="grid gap-2 sm:grid-cols-2">
							<FormControl
								v-for="(_, i) in values"
								:key="i"
								v-model="values[i]"
								:label="`Value {{${i + 1}}}`"
							/>
						</div>
						<div
							class="whitespace-pre-wrap rounded-xl bg-surface-green-1 px-3 py-2 text-sm text-ink-gray-8"
						>
							{{ preview }}
						</div>
					</template>
				</template>
				<ErrorMessage :message="error" />
			</div>
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				label="Send template"
				:loading="sending"
				:disabled="!ready"
				@click="send"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import {
	Button,
	Dialog,
	ErrorMessage,
	FormControl,
	LoadingIndicator,
	TextInput,
	call,
} from "frappe-ui";
import LucideSearch from "~icons/lucide/search";
import { errorText } from "@/utils/errors";

// The approved templates of a Meta WhatsApp chat (api/compose.meta_templates), with a value for each
// {{n}} of the body and a preview of what the customer gets.
const show = defineModel({ type: Boolean, default: false });
const props = defineProps({ chat: { type: Object, required: true } });
const emit = defineEmits(["sent"]);

const templates = ref([]);
const loading = ref(false);
const sending = ref(false);
const error = ref("");
const search = ref("");
const picked = ref(null);
const values = ref([]);

const shown = computed(() => {
	const q = search.value.trim().toLowerCase();
	return q
		? templates.value.filter((t) => `${t.label} ${t.body}`.toLowerCase().includes(q))
		: templates.value;
});
const preview = computed(() =>
	(picked.value?.body || "").replace(
		/\{\{(\d+)\}\}/g,
		(m, n) => values.value[n - 1] || `{{${n}}}`
	)
);
const ready = computed(
	() => picked.value && values.value.every((v) => (v || "").trim()) && !sending.value
);

function pick(t) {
	picked.value = t;
	values.value = Array.from({ length: t.params }, () => "");
	error.value = "";
}

watch(show, async (open) => {
	if (!open) return;
	picked.value = null;
	search.value = "";
	error.value = "";
	loading.value = true;
	try {
		templates.value = await call("periskope_connector.api.compose.meta_templates", {
			chat: props.chat.name,
		});
	} catch (e) {
		error.value = errorText(e, "Could not load the templates.");
	} finally {
		loading.value = false;
	}
});

async function send() {
	sending.value = true;
	error.value = "";
	try {
		const message = await call("periskope_connector.api.compose.send_template", {
			chat: props.chat.name,
			template: picked.value.name,
			params: values.value,
		});
		emit("sent", message);
		show.value = false;
	} catch (e) {
		error.value = errorText(e, "The template was not sent.");
	} finally {
		sending.value = false;
	}
}
</script>
