<template>
	<Dropdown :options="options">
		<button
			class="rounded text-ink-gray-5 hover:bg-surface-gray-2"
			:class="
				isMobile
					? 'grid size-9 shrink-0 place-items-center'
					: 'invisible p-1 group-hover:visible data-[state=open]:visible'
			"
			aria-label="More actions"
		>
			<LucideEllipsisVertical class="size-4" />
		</button>
	</Dropdown>

	<!-- the dialogs exist only while open: a long chat has one menu per message -->
	<AiDetailsDialog v-if="aiOpen" v-model="aiOpen" :message="message.name" />
	<!-- react -->
	<Dialog v-if="reacting" v-model="reacting" :options="{ title: 'React', size: 'xs' }">
		<template #body-content>
			<div class="flex flex-wrap justify-center gap-1">
				<button
					v-for="e in EMOJI"
					:key="e"
					class="rounded-lg p-1.5 text-2xl hover:bg-surface-gray-2"
					:class="e === mine ? 'bg-surface-gray-3' : ''"
					:aria-label="`React with ${e}`"
					@click="react(e === mine ? '' : e)"
				>
					{{ e }}
				</button>
			</div>
			<p class="mt-2 text-center text-xs text-ink-gray-5">
				{{
					mine
						? "Click your reaction again to take it back."
						: "The customer sees the reaction."
				}}
			</p>
			<ErrorMessage class="mt-2" :message="error" />
		</template>
	</Dialog>

	<!-- edit -->
	<Dialog v-if="editing" v-model="editing" :options="{ title: 'Edit message', size: 'md' }">
		<template #body-content>
			<FormControl v-model="newText" type="textarea" :rows="4" />
			<p class="mt-1 text-xs text-ink-gray-5">
				WhatsApp shows "Edited" on it. Possible only in its first 15 minutes.
			</p>
			<ErrorMessage class="mt-2" :message="error" />
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				label="Save"
				:loading="busy"
				:disabled="!newText.trim() || newText.trim() === message.body"
				@click="edit"
			/>
		</template>
	</Dialog>

	<!-- forward -->
	<Dialog v-if="forwarding" v-model="forwarding" :options="{ title: 'Forward to', size: 'md' }">
		<template #body-content>
			<FormControl v-model="search" placeholder="Search chats on this number" />
			<div
				class="mt-2 max-h-72 divide-y divide-outline-gray-1 overflow-y-auto rounded-lg border border-outline-gray-2"
			>
				<label
					v-for="c in chats"
					:key="c.name"
					class="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm text-ink-gray-8 hover:bg-surface-gray-1"
				>
					<input v-model="picked" type="checkbox" :value="c.name" class="rounded" />
					<span class="min-w-0 flex-1 truncate">{{
						c.chat_name || "+" + c.contact_number
					}}</span>
					<LucideUsersRound v-if="c.is_group" class="size-3.5 text-ink-gray-5" />
				</label>
				<p v-if="!chats.length" class="px-3 py-4 text-center text-sm text-ink-gray-5">
					No chats found.
				</p>
			</div>
			<p v-if="results.length" class="mt-2 text-xs text-ink-gray-6">
				<span
					v-for="r in results"
					:key="r.chat"
					:class="r.ok ? 'text-ink-green-7' : 'text-ink-red-7'"
				>
					{{ r.title }}: {{ r.ok ? "forwarded" : "not forwarded" }}.
				</span>
			</p>
			<ErrorMessage class="mt-2" :message="error" />
		</template>
		<template #actions>
			<Button
				class="w-full"
				variant="solid"
				:label="picked.length ? `Forward to ${picked.length}` : 'Forward'"
				:loading="busy"
				:disabled="!picked.length"
				@click="forward"
			/>
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import dayjs from "dayjs";
import { Button, Dialog, Dropdown, ErrorMessage, FormControl, call, toast } from "frappe-ui";
import LucideEllipsisVertical from "~icons/lucide/ellipsis-vertical";
import LucideUsersRound from "~icons/lucide/users-round";
import AiDetailsDialog from "@/components/AiDetailsDialog.vue";
import { boot } from "@/boot";
import { ask } from "@/utils/confirm";
import { errorText, showError } from "@/utils/errors";
import { isMobile } from "@/utils/screen";

// More actions on one WhatsApp message (api/message_actions.py): react, edit and delete our own,
// forward, pin. Periskope numbers only; the customer sees each of them.
const props = defineProps({
	message: { type: Object, required: true },
	chat: { type: Object, required: true },
	// whether WhatsApp's own actions apply (not on a Meta number or an unsent message)
	whatsapp: { type: Boolean, default: true },
	// the bubble's other actions (reply, create a deal or an order): a phone has them here
	extra: { type: Array, default: () => [] },
});
const emit = defineEmits(["updated"]);

const API = "periskope_connector.api.message_actions.";
const EMOJI = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👏", "🔥", "✅", "🎉"];
const busy = ref(false);
const error = ref("");
const reacting = ref(false);
const editing = ref(false);
const forwarding = ref(false);
const newText = ref("");
const search = ref("");
const chats = ref([]);
const picked = ref([]);
const results = ref([]);

const outgoing = computed(() => props.message.direction === "Outgoing");
const ours = computed(
	() => outgoing.value && (props.message.sent_by === boot.user || boot.is_manager)
);
const mine = computed(() => (props.message.reactions || {})[props.chat.whatsapp_number] || "");
const canEdit = computed(
	() =>
		ours.value &&
		["text", "chat", "image", "video", "document"].includes(
			props.message.message_type || "text"
		) &&
		dayjs().diff(dayjs(props.message.message_timestamp), "minute") < 15
);

const options = computed(() => [
	...props.extra,
	// what the AI decided about a customer's message (and, for managers, correcting it)
	...(outgoing.value
		? []
		: [{ label: "What the AI decided", onClick: () => (aiOpen.value = true) }]),
	...(props.whatsapp ? whatsappOptions.value : []),
]);
const aiOpen = ref(false);
const whatsappOptions = computed(() => [
	{ label: mine.value ? "Change reaction" : "React", onClick: () => open(reacting) },
	{ label: "Forward", onClick: openForward },
	{ label: "Pin for 1 day", onClick: () => pin(1, 1) },
	{ label: "Pin for 7 days", onClick: () => pin(1, 7) },
	{ label: "Pin for 30 days", onClick: () => pin(1, 30) },
	{ label: "Unpin", onClick: () => pin(0) },
	...(canEdit.value
		? [
				{
					label: "Edit",
					onClick: () => ((newText.value = props.message.body || ""), open(editing)),
				},
		  ]
		: []),
	...(ours.value ? [{ label: "Delete for everyone", theme: "red", onClick: remove }] : []),
]);

function open(flag) {
	error.value = "";
	flag.value = true;
}

async function run(method, args, done) {
	busy.value = true;
	error.value = "";
	try {
		const r = await call(API + method, { message: props.message.name, ...args });
		done?.(r);
		return r;
	} catch (e) {
		error.value = errorText(e, "That did not work.");
		return null;
	} finally {
		busy.value = false;
	}
}

async function react(emoji) {
	await run("react", { emoji }, (m) => {
		emit("updated", m);
		reacting.value = false;
	});
}

async function edit() {
	await run("edit", { text: newText.value }, (m) => {
		emit("updated", m);
		editing.value = false;
	});
}

async function remove() {
	const ok = await ask(
		"It disappears for the customer too. WhatsApp allows this only for a while after sending.",
		{
			title: "Delete for everyone?",
			confirmLabel: "Delete",
			danger: true,
		}
	);
	if (!ok) return;
	const r = await run("delete", {}, (m) => emit("updated", m));
	if (!r) showError(null, error.value);
}

async function pin(pinned, days) {
	const r = await run("pin", { pinned, days });
	if (r)
		toast.success(
			pinned ? `Pinned in the chat for ${days} ${days === 1 ? "day" : "days"}` : "Unpinned"
		);
	else showError(null, error.value);
}

let searchTimer;
async function loadChats() {
	try {
		const r = await call("periskope_connector.api.inbox.get_chats", {
			filters: { number: props.chat.whatsapp_number, search: search.value || undefined },
			page_length: 40,
		});
		chats.value = (r.chats || r.rows || []).filter((c) => c.name !== props.chat.name);
	} catch {
		chats.value = [];
	}
}
function openForward() {
	picked.value = [];
	results.value = [];
	search.value = "";
	open(forwarding);
	loadChats();
}
watch(search, () => {
	clearTimeout(searchTimer);
	searchTimer = setTimeout(loadChats, 250);
});

async function forward() {
	const r = await run("forward", { chats: JSON.stringify(picked.value) });
	if (r) {
		results.value = r.results;
		if (r.results.every((x) => x.ok)) {
			toast.success(
				`Forwarded to ${r.results.length} ${r.results.length === 1 ? "chat" : "chats"}`
			);
			forwarding.value = false;
		}
	}
}
</script>
