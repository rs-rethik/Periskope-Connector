<template>
	<div class="flex h-full min-h-0">
		<div class="min-w-0 flex-1">
			<ChatView :name="$route.params.chat" @loaded="chat = $event">
				<template #actions>
					<Button
						variant="ghost"
						label="Open in inbox"
						@click="
							$router.push({ name: 'Inbox', params: { chat: $route.params.chat } })
						"
					/>
					<Button
						variant="ghost"
						label="Back to list"
						@click="$router.push({ name: 'Conversations' })"
					/>
				</template>
			</ChatView>
		</div>
		<div class="hidden w-[300px] shrink-0 lg:block">
			<ContextPanel :chat="chat" />
		</div>
	</div>
</template>

<script setup>
import { ref } from "vue";
import { Button } from "frappe-ui";
import ChatView from "@/components/ChatView.vue";
import ContextPanel from "@/components/ContextPanel.vue";

const chat = ref(null);
</script>
