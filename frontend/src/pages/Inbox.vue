<template>
	<div class="flex h-full min-h-0">
		<!-- a phone shows the list or the open chat, not both -->
		<div
			v-if="!isMobile || !chatName"
			class="shrink-0 border-r border-outline-gray-1"
			:class="isMobile ? 'w-full' : 'w-[340px]'"
		>
			<ChatList ref="list" :selected="chatName" @select="open" />
		</div>
		<div v-if="!isMobile || chatName" class="min-w-0 flex-1">
			<ChatView
				v-if="chatName"
				:key="chatName"
				:name="chatName"
				:back="isMobile"
				@back="$router.push({ name: 'Inbox' })"
				@loaded="chat = $event"
			>
				<template v-if="!isWide" #actions>
					<Button label="CRM" @click="panelOpen = true">
						<template #prefix><LucideContact class="size-4" /></template>
					</Button>
				</template>
			</ChatView>
			<div
				v-else
				class="flex h-full flex-col items-center justify-center gap-2 bg-surface-gray-1 text-ink-gray-5"
			>
				<LucideMessagesSquare class="size-10" />
				<div class="text-base">Pick a chat to read it</div>
				<div class="text-sm">j / k move between chats</div>
			</div>
		</div>
		<div v-if="chatName && isWide" class="w-[300px] shrink-0">
			<ContextPanel :chat="chat" />
		</div>
		<!-- narrower screens: the CRM panel slides over the chat on demand -->
		<div
			v-if="chatName && !isWide && panelOpen"
			role="dialog"
			class="fixed inset-0 z-30 flex justify-end"
		>
			<div class="hidden flex-1 bg-black/30 sm:block" @click="panelOpen = false" />
			<div class="flex h-full w-full flex-col bg-surface-base shadow-2xl sm:w-[360px]">
				<div class="flex h-12 shrink-0 items-center border-b border-outline-gray-1 px-3">
					<span class="text-base font-medium text-ink-gray-9">CRM</span>
					<Button
						class="ml-auto"
						variant="ghost"
						title="Close"
						@click="panelOpen = false"
					>
						<template #icon><LucideX class="size-4" /></template>
					</Button>
				</div>
				<div class="min-h-0 flex-1"><ContextPanel :chat="chat" /></div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import LucideMessagesSquare from "~icons/lucide/messages-square";
import ChatList from "@/components/ChatList.vue";
import ChatView from "@/components/ChatView.vue";
import ContextPanel from "@/components/ContextPanel.vue";
import { Button } from "frappe-ui";
import LucideContact from "~icons/lucide/contact";
import LucideX from "~icons/lucide/x";
import { isMobile, isWide } from "@/utils/screen";

const route = useRoute();
const router = useRouter();
const list = ref(null);
const chat = ref(null);
const panelOpen = ref(false);
const chatName = computed(() => route.params.chat || "");

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
	const rows = list.value?.chats || [];
	const i = rows.findIndex((c) => c.name === chatName.value);
	const next = rows[e.key === "j" ? i + 1 : Math.max(i - 1, 0)];
	if (next) open(next.name);
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>
