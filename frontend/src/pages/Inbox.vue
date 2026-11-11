<template>
	<div class="flex h-full min-h-0 flex-col">
		<div v-if="!isMobile" class="flex flex-wrap items-end justify-between gap-3 px-5 pt-4">
			<h1 class="text-[28px] font-semibold leading-none tracking-tight text-ink-gray-9">
				Inbox
			</h1>
			<div class="flex flex-wrap gap-2">
				<NumberSwitcher v-if="boot.numbers.length > 1" />
				<div
					v-for="s in stats"
					:key="s.label"
					class="min-w-[92px] rounded-xl border border-outline-gray-1 bg-surface-base px-3 py-1.5 text-center shadow-sm"
				>
					<div class="text-lg font-semibold leading-tight tabular-nums text-ink-gray-9">
						{{ s.value }}
					</div>
					<div class="flex items-center justify-center gap-1 text-xs text-ink-gray-6">
						<component :is="s.icon" class="size-3" :class="s.tone" />{{ s.label }}
					</div>
				</div>
			</div>
		</div>
		<div class="split flex min-h-0 flex-1">
			<!-- a phone shows the list or the open chat, not both -->
			<!-- (hidden, not unmounted: Back finds its tab, search and place as they were) -->
			<div
				v-show="!isMobile || !chatName"
				class="pane shrink-0"
				:class="isMobile ? 'w-full' : 'w-[340px]'"
			>
				<ChatList ref="list" :selected="chatName" @select="open" />
			</div>
			<div v-if="!isMobile || chatName" class="pane min-w-0 flex-1">
				<ChatView
					v-if="chatName"
					:key="chatName"
					:name="chatName"
					:back="isMobile"
					@back="$router.push({ name: 'Inbox' })"
					@loaded="chat = $event"
				>
					<template v-if="!isWide" #actions>
						<Button class="tap" label="CRM" @click="panelOpen = true">
							<template v-if="!isMobile" #prefix
								><LucideContact class="size-4"
							/></template>
						</Button>
					</template>
				</ChatView>
				<div
					v-else
					class="chat-dots flex h-full flex-col items-center justify-center gap-3 px-6 text-center"
				>
					<div class="grid size-20 place-items-center rounded-3xl bg-surface-green-1">
						<div
							class="grid size-12 place-items-center rounded-2xl bg-surface-green-2 text-ink-green-7"
						>
							<LucideMessagesSquare class="size-6" />
						</div>
					</div>
					<div class="text-xl font-semibold text-ink-gray-9">Pick a conversation</div>
					<div class="max-w-sm text-sm text-ink-gray-6">
						Replies go out from the WhatsApp number the chat is on. Your team sees the
						same inbox.
					</div>
					<div class="mt-1 flex items-center gap-3 text-sm text-ink-gray-5">
						<span class="flex items-center gap-1"
							><kbd class="kbd">j</kbd><kbd class="kbd">k</kbd> move through the
							list</span
						>
						<span class="flex items-center gap-1"
							><kbd class="kbd">/</kbd> quick replies</span
						>
					</div>
				</div>
			</div>
			<div
				v-if="chatName && isWide"
				class="pane relative shrink-0 transition-[width] duration-200"
				:class="crmCollapsed ? 'w-14' : 'w-[300px]'"
			>
				<!-- collapsed: a slim rail with the contact and a way back -->
				<div v-if="crmCollapsed" class="flex h-full flex-col items-center gap-3 py-3">
					<Button
						variant="ghost"
						tooltip="Show the CRM panel"
						aria-label="Show the CRM panel"
						@click="toggleCrm"
					>
						<template #icon><LucidePanelRightOpen class="size-4" /></template>
					</Button>
					<InitialsAvatar
						v-if="chat"
						size="sm"
						:name="chat.chat_name || chat.contact_number || chat.title"
					/>
					<button
						class="mt-1 text-xs font-semibold uppercase tracking-wider text-ink-gray-5 [writing-mode:vertical-rl]"
						@click="toggleCrm"
					>
						CRM
					</button>
				</div>
				<template v-else>
					<Button
						class="absolute right-2 top-2 z-10 !bg-surface-base shadow-sm"
						variant="ghost"
						tooltip="Hide the CRM panel"
						aria-label="Hide the CRM panel"
						@click="toggleCrm"
					>
						<template #icon><LucidePanelRightClose class="size-4" /></template>
					</Button>
					<ContextPanel :key="chatName" :chat="chat" />
				</template>
			</div>
			<!-- narrower screens: the CRM panel slides over the chat on demand -->
			<div
				v-if="chatName && !isWide && panelOpen"
				role="dialog"
				class="fixed inset-0 z-30 flex justify-end"
			>
				<div class="hidden flex-1 bg-black/30 sm:block" @click="panelOpen = false" />
				<div
					class="safe-top safe-bottom flex h-full w-full flex-col bg-surface-base shadow-2xl sm:w-[360px]"
				>
					<div
						class="flex h-12 shrink-0 items-center border-b border-outline-gray-1 px-3"
					>
						<span class="text-base font-medium text-ink-gray-9">CRM</span>
						<Button
							class="tap ml-auto"
							variant="ghost"
							tooltip="Close"
							aria-label="Close"
							@click="panelOpen = false"
						>
							<template #icon><LucideX class="size-4" /></template>
						</Button>
					</div>
					<div class="min-h-0 flex-1">
						<ContextPanel :key="chatName" :chat="chat" />
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import LucideMessagesSquare from "~icons/lucide/messages-square";
import ChatList from "@/components/ChatList.vue";
import NumberSwitcher from "@/components/NumberSwitcher.vue";
import { boot } from "@/boot";
import ChatView from "@/components/ChatView.vue";
import ContextPanel from "@/components/ContextPanel.vue";
import { Button } from "frappe-ui";
import LucideContact from "~icons/lucide/contact";
import LucideX from "~icons/lucide/x";
import { isMobile, isWide } from "@/utils/screen";
import { inbox } from "@/stores/inbox";
import LucideMail from "~icons/lucide/mail";
import LucideHourglass from "~icons/lucide/hourglass";
import LucideTimerOff from "~icons/lucide/timer-off";
import LucideInbox from "~icons/lucide/inbox";
import LucidePanelRightClose from "~icons/lucide/panel-right-close";
import LucidePanelRightOpen from "~icons/lucide/panel-right-open";
import InitialsAvatar from "@/components/InitialsAvatar.vue";

const route = useRoute();
const router = useRouter();
const list = ref(null);
const chat = ref(null);
const panelOpen = ref(false);

// The CRM panel beside the chat can be folded to a rail; the choice is kept per browser.
const CRM_KEY = "periskope-crm-panel-collapsed";
function readCollapsed() {
	try {
		return localStorage.getItem(CRM_KEY) === "1";
	} catch {
		return false;
	}
}
const crmCollapsed = ref(readCollapsed());
function toggleCrm() {
	crmCollapsed.value = !crmCollapsed.value;
	try {
		localStorage.setItem(CRM_KEY, crmCollapsed.value ? "1" : "0");
	} catch {
		// private window: the choice lasts for this visit only
	}
}
const chatName = computed(() => route.params.chat || "");
const stats = computed(() => [
	{
		label: "Unread",
		value: inbox.counts.unread ?? "–",
		icon: LucideMail,
		tone: "text-ink-green-7",
	},
	{
		label: "Waiting on us",
		value: inbox.counts.unanswered ?? "–",
		icon: LucideHourglass,
		tone: "text-ink-amber-7",
	},
	{
		label: "Past SLA",
		value: inbox.counts.breached ?? "–",
		icon: LucideTimerOff,
		tone: "text-ink-red-6",
	},
	{ label: "Open", value: inbox.counts.all ?? "–", icon: LucideInbox, tone: "text-ink-blue-7" },
]);

function open(name) {
	chat.value = null;
	panelOpen.value = false;
	router.push({ name: "Inbox", params: { chat: name } });
}

// Keyboard: j / k for the next / previous chat in the list.
function onKey(e) {
	if (e.key === "Escape" && panelOpen.value) return (panelOpen.value = false);
	if (e.target.closest("input, textarea, [contenteditable]")) return;
	if (!["j", "k"].includes(e.key)) return;
	// a dialog, a menu or the image viewer is open: the keys are theirs
	if (document.querySelector('[role="dialog"], [role="menu"]')) return;
	const rows = list.value?.chats || [];
	const i = rows.findIndex((c) => c.name === chatName.value);
	const next = rows[e.key === "j" ? i + 1 : Math.max(i - 1, 0)];
	if (next) open(next.name);
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
