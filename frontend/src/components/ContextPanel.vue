<template>
	<div
		class="flex h-full flex-col overflow-y-auto border-l border-outline-gray-1 bg-surface-base"
	>
		<div v-if="chat" class="flex flex-col gap-5 p-4">
			<section>
				<div class="text-xs font-medium uppercase text-ink-gray-5">Contact</div>
				<div class="mt-1 text-base text-ink-gray-9">{{ chat.title }}</div>
				<div v-if="chat.contact_number" class="text-sm text-ink-gray-6">
					+{{ chat.contact_number }}
				</div>
				<div v-if="chat.is_group" class="text-sm text-ink-gray-6">WhatsApp group</div>
				<div class="mt-1 text-xs text-ink-gray-5">On {{ chat.number_label }}</div>
			</section>

			<!-- The lead or deal -->
			<section>
				<div class="mb-1 flex items-center text-xs font-medium uppercase text-ink-gray-5">
					CRM
					<Button
						v-if="record && !record.hidden"
						class="ml-auto"
						size="sm"
						variant="ghost"
						label="Change"
						@click="openLink"
					/>
				</div>
				<div v-if="loading && !ctx" class="py-2"><LoadingIndicator class="size-4" /></div>
				<div
					v-else-if="loadError && !ctx"
					class="flex items-center gap-2 text-sm text-ink-gray-6"
				>
					Could not load the CRM record.
					<Button size="sm" label="Retry" @click="load" />
				</div>
				<template v-else-if="record && record.hidden">
					<div class="text-sm text-ink-gray-6">
						Linked to a {{ kind }} you cannot open.
					</div>
				</template>
				<div v-else-if="record" class="rounded-lg border border-outline-gray-1 p-3">
					<div class="flex items-start gap-2">
						<div class="min-w-0 flex-1">
							<a
								:href="record.route"
								target="_blank"
								class="flex items-center gap-1 text-base font-medium text-ink-gray-9 hover:underline"
							>
								<span class="truncate">{{ record.title }}</span>
								<LucideExternalLink class="size-3 shrink-0" />
							</a>
							<div class="text-xs text-ink-gray-5">
								{{ kindLabel }} · {{ record.name }}
							</div>
						</div>
					</div>
					<div class="mt-2">
						<Dropdown v-if="record.can_edit" :options="statusOptions">
							<Button size="sm" :label="record.status">
								<template #prefix
									><span
										class="size-2 rounded-full"
										:style="{ background: statusColor }"
								/></template>
							</Button>
						</Dropdown>
						<Badge v-else :label="record.status" variant="subtle" />
					</div>
					<dl class="mt-3 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-sm">
						<dt class="text-ink-gray-5">Owner</dt>
						<dd class="truncate text-ink-gray-8">{{ record.owner_name || "—" }}</dd>
						<template v-if="record.value">
							<dt class="text-ink-gray-5">Value</dt>
							<dd class="text-ink-gray-8">
								{{ money(record.value, record.currency) }}
							</dd>
						</template>
						<template v-if="record.organization">
							<dt class="text-ink-gray-5">Company</dt>
							<dd class="truncate text-ink-gray-8">{{ record.organization }}</dd>
						</template>
						<template v-if="record.email">
							<dt class="text-ink-gray-5">Email</dt>
							<dd class="truncate text-ink-gray-8">{{ record.email }}</dd>
						</template>
						<dt class="text-ink-gray-5">Updated</dt>
						<dd class="text-ink-gray-8">{{ fromNow(record.modified) }}</dd>
					</dl>
					<ErrorMessage
						v-if="
							error &&
							!linkDialog &&
							!createDialog &&
							!noteDialog &&
							!taskDialog &&
							!lostDialog
						"
						class="mt-2"
						:message="error"
					/>
					<button
						v-if="ctx.linked_by_hand"
						class="mt-2 text-xs text-ink-gray-5 hover:text-ink-gray-8 hover:underline"
						@click="unlink"
					>
						Linked by hand · undo
					</button>
				</div>
				<div v-else class="flex flex-col gap-2">
					<div class="text-sm text-ink-gray-5">Not linked to a lead or deal.</div>
					<div class="flex gap-2">
						<Button size="sm" label="Link existing" @click="openLink" />
						<Button
							size="sm"
							variant="solid"
							label="Create lead"
							@click="openCreate"
						/>
					</div>
				</div>
			</section>

			<section v-if="record && record.summary">
				<div class="mb-1 text-xs font-medium uppercase text-ink-gray-5">
					WhatsApp summary
				</div>
				<div class="text-sm text-ink-gray-7">
					<div
						v-for="(line, i) in summaryLines"
						:key="i"
						:class="line.heading ? 'mt-2 font-medium text-ink-gray-8 first:mt-0' : ''"
					>
						{{ line.text }}
					</div>
				</div>
			</section>

			<section v-if="record && !record.hidden">
				<div class="mb-1 flex items-center text-xs font-medium uppercase text-ink-gray-5">
					Open tasks
					<Button
						class="ml-auto"
						size="sm"
						variant="ghost"
						label="Add"
						@click="
							error = '';
							taskDialog = true;
						"
					>
						<template #prefix><LucidePlus class="size-3.5" /></template>
					</Button>
				</div>
				<div v-if="!record.tasks.length" class="text-sm text-ink-gray-5">None</div>
				<div v-for="t in record.tasks" :key="t.name" class="flex items-start gap-2 py-1">
					<input
						type="checkbox"
						class="mt-0.5 rounded border-outline-gray-3"
						:title="'Mark done'"
						:disabled="busy"
						@change="completeTask(t, $event)"
					/>
					<div class="min-w-0 flex-1">
						<div class="text-sm text-ink-gray-8">{{ t.title }}</div>
						<div class="text-xs text-ink-gray-5">
							<span v-if="t.due_date">Due {{ fromNow(t.due_date) }} · </span
							>{{ t.priority }}
							<span v-if="t.assigned_to"> · {{ t.assigned_to }}</span>
						</div>
					</div>
				</div>
			</section>

			<section v-if="record && !record.hidden">
				<div class="mb-1 flex items-center text-xs font-medium uppercase text-ink-gray-5">
					CRM notes
					<Button
						class="ml-auto"
						size="sm"
						variant="ghost"
						label="Add"
						@click="
							error = '';
							noteDialog = true;
						"
					>
						<template #prefix><LucidePlus class="size-3.5" /></template>
					</Button>
				</div>
				<div v-if="!record.notes.length" class="text-sm text-ink-gray-5">None</div>
				<div
					v-for="n in record.notes"
					:key="n.name"
					class="border-b border-outline-gray-1 py-1.5 last:border-0"
				>
					<div class="text-sm font-medium text-ink-gray-8">{{ n.title }}</div>
					<div class="line-clamp-3 text-sm text-ink-gray-6">{{ plain(n.content) }}</div>
					<div class="text-xs text-ink-gray-5">{{ fromNow(n.creation) }}</div>
				</div>
			</section>

			<section v-if="ctx && ctx.tags.length">
				<div class="mb-1 text-xs font-medium uppercase text-ink-gray-5">AI tags</div>
				<div class="flex flex-wrap gap-1">
					<Badge
						v-for="t in ctx.tags"
						:key="t.tag"
						:label="t.count > 1 ? `${t.tag} · ${t.count}` : t.tag"
						variant="subtle"
						theme="blue"
					/>
				</div>
			</section>

			<section v-if="ctx && ctx.other_chats.length">
				<div class="mb-1 text-xs font-medium uppercase text-ink-gray-5">
					On our other numbers
				</div>
				<button
					v-for="o in ctx.other_chats"
					:key="o.name"
					class="flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
					@click="$router.push({ name: 'Inbox', params: { chat: o.name } })"
				>
					<span class="text-sm text-ink-gray-8">{{ o.number_label }}</span>
					<span class="truncate text-xs text-ink-gray-5">{{
						o.last_message_preview
					}}</span>
				</button>
			</section>

			<section>
				<div class="text-xs font-medium uppercase text-ink-gray-5">Assigned to</div>
				<div class="mt-1 text-sm text-ink-gray-8">
					{{ chat.assigned_to?.length ? chat.assigned_to.join(", ") : "Nobody" }}
				</div>
			</section>
		</div>

		<!-- Link to an existing lead or deal -->
		<Dialog v-model="linkDialog" :options="{ title: 'Link to a lead or deal' }">
			<template #body-content>
				<TextInput
					v-model="query"
					placeholder="Search name, company, number or email"
					:debounce="300"
				/>
				<div class="mt-2 max-h-72 overflow-y-auto">
					<button
						v-for="r in results"
						:key="r.doctype + r.name"
						class="flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
						:disabled="busy"
						@click="link(r)"
					>
						<span class="text-sm text-ink-gray-8">
							{{ r.title }}
							<span class="text-xs text-ink-gray-5"
								>· {{ r.doctype === "CRM Deal" ? "Deal" : "Lead" }}</span
							>
						</span>
						<span class="truncate text-xs text-ink-gray-5">{{ r.subtitle }}</span>
					</button>
					<div v-if="searching && !results.length" class="p-2 text-sm text-ink-gray-5">
						Searching…
					</div>
					<div v-else-if="!results.length" class="p-2 text-sm text-ink-gray-5">
						Nothing found.
					</div>
				</div>
				<ErrorMessage class="mt-2" :message="error" />
			</template>
		</Dialog>

		<!-- Create a lead -->
		<Dialog v-model="createDialog" :options="{ title: 'Create lead' }">
			<template #body-content>
				<form class="flex flex-col gap-3" @submit.prevent="create">
					<div class="grid grid-cols-2 gap-3">
						<FormControl v-model="lead.first_name" label="First name" autofocus />
						<FormControl v-model="lead.last_name" label="Last name" />
					</div>
					<FormControl v-model="lead.organization" label="Company" />
					<FormControl v-model="lead.email" label="Email" />
					<div v-if="chat?.contact_number" class="text-sm text-ink-gray-5">
						Mobile: +{{ chat.contact_number }} · Source: WhatsApp · Owner: you
					</div>
					<ErrorMessage :message="error" />
					<button type="submit" class="hidden" />
				</form>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Create lead"
					:loading="busy"
					:disabled="!lead.first_name"
					@click="create"
				/>
			</template>
		</Dialog>

		<Dialog v-model="lostDialog" :options="{ title: `Mark as ${lost.status}` }">
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl
						v-model="lost.reason"
						type="select"
						label="Why was it lost?"
						:options="reasons"
					/>
					<FormControl
						v-model="lost.notes"
						type="textarea"
						:label="lost.reason === 'Other' ? 'Details (needed for Other)' : 'Details'"
						:rows="3"
					/>
					<ErrorMessage :message="error" />
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					theme="red"
					:label="`Mark as ${lost.status}`"
					:loading="busy"
					:disabled="!lost.reason || (lost.reason === 'Other' && !lost.notes)"
					@click="markLost"
				/>
			</template>
		</Dialog>

		<Dialog v-model="noteDialog" :options="{ title: 'Add a CRM note' }">
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl
						v-model="note.title"
						label="Title"
						placeholder="Note from WhatsApp"
					/>
					<FormControl
						v-model="note.content"
						type="textarea"
						label="Note"
						:rows="5"
						autofocus
					/>
					<ErrorMessage :message="error" />
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Add note"
					:loading="busy"
					:disabled="!note.content"
					@click="addNote"
				/>
			</template>
		</Dialog>

		<Dialog v-model="taskDialog" :options="{ title: 'Add a task' }">
			<template #body-content>
				<form class="flex flex-col gap-3" @submit.prevent="addTask">
					<FormControl v-model="task.title" label="Title" autofocus />
					<FormControl
						v-model="task.description"
						type="textarea"
						label="Details"
						:rows="3"
					/>
					<div class="grid grid-cols-2 gap-3">
						<FormControl v-model="task.due_date" type="date" label="Due" />
						<FormControl
							v-model="task.priority"
							type="select"
							label="Priority"
							:options="['Low', 'Medium', 'High']"
						/>
					</div>
					<ErrorMessage :message="error" />
					<button type="submit" class="hidden" />
				</form>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Add task"
					:loading="busy"
					:disabled="!task.title"
					@click="addTask"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import {
	Badge,
	Button,
	Dialog,
	Dropdown,
	ErrorMessage,
	FormControl,
	LoadingIndicator,
	TextInput,
	call,
} from "frappe-ui";
import LucideExternalLink from "~icons/lucide/external-link";
import LucidePlus from "~icons/lucide/plus";
import { changed, inbox } from "@/stores/inbox";
import { fromNow } from "@/utils/format";

const props = defineProps({ chat: { type: Object, default: null } });

const ctx = ref(null);
const loading = ref(false);
const loadError = ref(false);
const searching = ref(false);
let loadSeq = 0;
let searchSeq = 0;
const busy = ref(false);
const error = ref("");
const linkDialog = ref(false);
const createDialog = ref(false);
const noteDialog = ref(false);
const taskDialog = ref(false);
const lostDialog = ref(false);
const reasons = ref([]);
const lost = ref({ status: "", reason: "", notes: "" });
const query = ref("");
const results = ref([]);
const lead = ref({ first_name: "", last_name: "", organization: "", email: "" });
const note = ref({ title: "", content: "" });
const task = ref({ title: "", description: "", due_date: "", priority: "Medium" });

const record = computed(() => ctx.value?.record || null);
// The summary's section titles (writeback.summary) shown as headings.
const SUMMARY_HEADINGS = ["What the customer wants", "What we answered", "Still pending"];
const summaryLines = computed(() =>
	(record.value?.summary || "")
		.split("\n")
		.filter((l) => l.trim())
		.map((l) => ({ text: l, heading: SUMMARY_HEADINGS.includes(l.trim()) }))
);
const kind = computed(() => (record.value?.doctype === "CRM Deal" ? "deal" : "lead"));
const kindLabel = computed(() => (record.value?.doctype === "CRM Deal" ? "Deal" : "Lead"));
const statusColor = computed(
	() => record.value?.statuses?.find((s) => s.value === record.value.status)?.color || "gray"
);
const statusOptions = computed(() =>
	(record.value?.statuses || []).map((s) => ({
		label: s.value,
		onClick: () =>
			s.type === "Lost" ? askLost(s.value) : run("set_status", { status: s.value }),
	}))
);

function plain(html) {
	const div = document.createElement("div");
	div.innerHTML = html || "";
	return div.textContent || "";
}

function money(value, currency) {
	try {
		return new Intl.NumberFormat(undefined, {
			style: "currency",
			currency: currency || "INR",
			maximumFractionDigits: 0,
		}).format(value);
	} catch (e) {
		return value;
	}
}

function message(e) {
	return e?.messages?.[0] || e?.message || "That did not work.";
}

// Only the newest load fills the panel, so a slow answer for the previous chat never shows here.
async function load() {
	if (!props.chat?.name) return;
	const seq = ++loadSeq;
	loading.value = true;
	try {
		const out = await call("periskope_connector.api.crm_panel.get_context", {
			chat_name: props.chat.name,
		});
		if (seq !== loadSeq) return;
		ctx.value = out;
		loadError.value = false;
	} catch (e) {
		// a failed refresh keeps what is shown; a failed first load says so (not "not linked")
		if (seq === loadSeq) loadError.value = true;
	} finally {
		if (seq === loadSeq) loading.value = false;
	}
}

// One change at a time, so two quick clicks cannot undo each other.
async function run(method, args = {}) {
	if (busy.value) return false;
	busy.value = true;
	error.value = "";
	try {
		ctx.value = await call(`periskope_connector.api.crm_panel.${method}`, {
			chat_name: props.chat.name,
			...args,
		});
		return true;
	} catch (e) {
		error.value = message(e);
		return false;
	} finally {
		busy.value = false;
	}
}

async function openLink() {
	error.value = "";
	query.value = props.chat?.chat_name || "";
	linkDialog.value = true;
	await search();
}

async function search() {
	const seq = ++searchSeq;
	searching.value = true;
	try {
		const out = await call("periskope_connector.api.crm_panel.search_records", {
			query: query.value,
		});
		if (seq === searchSeq) results.value = out;
	} catch (e) {
		if (seq === searchSeq) error.value = message(e);
	} finally {
		if (seq === searchSeq) searching.value = false;
	}
}

async function link(r) {
	if (await run("link_record", { doctype: r.doctype, name: r.name })) linkDialog.value = false;
}

function openCreate() {
	error.value = "";
	const [first, ...rest] = (props.chat?.chat_name || "").split(" ");
	lead.value = {
		first_name: first || "",
		last_name: rest.join(" "),
		organization: "",
		email: "",
	};
	createDialog.value = true;
}

async function create() {
	if (!lead.value.first_name) return; // Enter in the form skips the button's own check
	if (await run("create_lead", lead.value)) createDialog.value = false;
}

async function addNote() {
	if (await run("add_note", note.value)) {
		noteDialog.value = false;
		note.value = { title: "", content: "" };
	}
}

async function addTask() {
	if (!task.value.title) return;
	if (await run("add_task", task.value)) {
		taskDialog.value = false;
		task.value = { title: "", description: "", due_date: "", priority: "Medium" };
	}
}

async function askLost(status) {
	error.value = "";
	lost.value = { status, reason: "", notes: "" };
	try {
		if (!reasons.value.length)
			reasons.value = await call("periskope_connector.api.crm_panel.lost_reasons");
	} catch (e) {
		// the reason list stays empty; the dialog still opens and the server says what is missing
	}
	lostDialog.value = true;
}

async function markLost() {
	const ok = await run("set_status", {
		status: lost.value.status,
		lost_reason: lost.value.reason,
		lost_notes: lost.value.notes,
	});
	if (ok) lostDialog.value = false;
}

async function completeTask(t, event) {
	// untick again if it did not work, so the box never claims a task is done when it is not
	if (!(await run("complete_task", { task: t.name }))) event.target.checked = false;
}

function unlink() {
	run("unlink_record");
}

watch(query, () => linkDialog.value && search());
watch(
	() => props.chat?.name,
	() => {
		ctx.value = null; // never show the previous chat's record while this one loads
		loadError.value = false;
		load();
	},
	{ immediate: true }
);
watch(
	() => inbox.version,
	() => {
		if (props.chat && changed(props.chat.name)) load();
	}
);
</script>
