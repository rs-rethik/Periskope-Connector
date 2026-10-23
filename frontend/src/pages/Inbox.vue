<template>
	<div class="flex h-full min-h-0">
		<div class="w-[340px] shrink-0 border-r border-outline-gray-1">
			<ChatList ref="list" :selected="chatName" @select="open" />
		</div>
		<div class="min-w-0 flex-1">
			<ChatView v-if="chatName" :key="chatName" :name="chatName" @loaded="chat = $event" />
			<div
				v-else
				class="flex h-full flex-col items-center justify-center gap-2 bg-surface-gray-1 text-ink-gray-5"
			>
				<LucideMessagesSquare class="size-10" />
				<div class="text-base">Pick a chat to read it</div>
				<div class="text-sm">j / k move between chats</div>
			</div>
		</div>
		<div v-if="chatName" class="hidden w-[300px] shrink-0 xl:block">
			<ContextPanel :chat="chat" />
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

const route = useRoute();
const router = useRouter();
const list = ref(null);
const chat = ref(null);
const chatName = computed(() => route.params.chat || "");

function open(name) {
	chat.value = null;
	router.push({ name: "Inbox", params: { chat: name } });
}

// Keyboard: j / k for the next / previous chat in the list.
function onKey(e) {
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
