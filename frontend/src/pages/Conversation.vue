<template>
	<div class="split flex h-full min-h-0">
		<div class="pane min-w-0 flex-1">
			<ChatView :name="$route.params.chat" @loaded="onLoaded">
				<template #actions>
					<Button
						variant="ghost"
						tooltip="Open in the chat view"
						aria-label="Open in the chat view"
						@click="
							$router.push({ name: 'Inbox', params: { chat: $route.params.chat } })
						"
					>
						<template #icon><LucideMessagesSquare class="size-4" /></template>
					</Button>
					<Button
						variant="ghost"
						tooltip="Back to the list"
						aria-label="Back to the list"
						@click="$router.push({ name: 'Conversations' })"
					>
						<template #icon><LucideList class="size-4" /></template>
					</Button>
				</template>
			</ChatView>
		</div>
		<div class="pane hidden w-[320px] shrink-0 flex-col lg:flex">
			<div
				class="flex gap-1 border-b border-l border-outline-gray-1 bg-surface-base px-3 py-2"
			>
				<Button
					size="sm"
					:variant="tab === 'crm' ? 'subtle' : 'ghost'"
					label="CRM"
					@click="tab = 'crm'"
				/>
				<Button
					size="sm"
					:variant="tab === 'activity' ? 'subtle' : 'ghost'"
					label="Activity"
					@click="tab = 'activity'"
				/>
			</div>
			<div class="min-h-0 flex-1">
				<ContextPanel v-if="tab === 'crm'" :key="$route.params.chat" :chat="chat" />
				<ActivityLog v-else ref="activity" :chat="$route.params.chat" />
			</div>
		</div>
	</div>
</template>

<script setup>
import { ref } from "vue";
import { Button } from "frappe-ui";
import LucideMessagesSquare from "~icons/lucide/messages-square";
import LucideList from "~icons/lucide/list";
import ChatView from "@/components/ChatView.vue";
import ContextPanel from "@/components/ContextPanel.vue";
import ActivityLog from "@/components/ActivityLog.vue";

const chat = ref(null);
const tab = ref("crm");
const activity = ref(null);

function onLoaded(c) {
	chat.value = c;
	activity.value?.reload();
}
</script>
