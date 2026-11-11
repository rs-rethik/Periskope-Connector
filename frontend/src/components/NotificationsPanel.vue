<template>
	<div class="flex flex-col">
		<div class="flex shrink-0 items-center gap-1 border-b border-outline-gray-1 px-3 py-2">
			<span class="text-base font-medium text-ink-gray-9">Notifications</span>
			<Button
				v-if="inbox.bell.unread"
				class="ml-auto"
				size="sm"
				variant="ghost"
				label="Mark all read"
				@click="readAll"
			/>
			<Button
				v-if="sheet"
				class="tap"
				:class="inbox.bell.unread ? '' : 'ml-auto'"
				variant="ghost"
				aria-label="Close"
				@click="emit('close')"
			>
				<template #icon><LucideX class="size-4" /></template>
			</Button>
		</div>
		<div class="overflow-y-auto" :class="sheet ? 'min-h-0 flex-1' : 'max-h-[28rem]'">
			<button
				v-for="n in inbox.bell.notifications"
				:key="n.name"
				class="flex w-full gap-2 border-b border-outline-gray-1 px-3 py-2 text-left last:border-0 hover:bg-surface-gray-1"
				@click="open(n)"
			>
				<span
					class="mt-1.5 size-2 shrink-0 rounded-full"
					:class="n.read ? 'bg-transparent' : 'bg-surface-blue-3'"
				/>
				<span class="min-w-0 flex-1">
					<span class="block text-sm text-ink-gray-8">{{ n.subject }}</span>
					<span class="text-xs text-ink-gray-5"
						>{{ kinds[n.type] || n.type }} · {{ fromNow(n.creation) }}</span
					>
				</span>
			</button>
			<div
				v-if="!inbox.bell.notifications.length"
				class="p-4 text-center text-sm text-ink-gray-5"
			>
				Nothing yet. You'll see assignments, mentions and overdue replies here.
			</div>
		</div>
	</div>
</template>

<script setup>
import { useRouter } from "vue-router";
import { Button, call } from "frappe-ui";
import LucideX from "~icons/lucide/x";
import { inbox, loadBell } from "@/stores/inbox";
import { fromNow } from "@/utils/format";
import { loadFailed } from "@/utils/load";

// The list of notifications: in the sidebar's popover (NotificationsBell), and as a full-screen
// sheet on a phone (App.vue), where it has its own Close button.
defineProps({ sheet: { type: Boolean, default: false } });
const emit = defineEmits(["close"]);
const router = useRouter();
const kinds = { Assignment: "Assigned to you", Mention: "Mentioned you", Alert: "SLA alert" };

// The chat opens at once; marking the notification read happens beside it and never holds it up.
function open(n) {
	if (!n.read)
		call("periskope_connector.api.notifications.mark_read", { name: n.name })
			.then(loadBell)
			.catch((e) => loadFailed(e, "Could not mark the notification read."));
	emit("close");
	router.push({ name: "Inbox", params: { chat: n.document_name } });
}

async function readAll() {
	try {
		await call("periskope_connector.api.notifications.mark_read");
		loadBell();
	} catch (e) {
		loadFailed(e, "Could not mark the notifications read.");
	}
}
</script>
