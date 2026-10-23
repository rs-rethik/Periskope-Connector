<template>
	<Popover placement="right-start">
		<template #target="{ togglePopover }">
			<SidebarItem label="Notifications" @click="togglePopover()">
				<template #prefix>
					<span class="relative grid size-4 place-items-center">
						<LucideBell class="size-4 text-ink-gray-6" />
						<span
							v-if="collapsed && inbox.bell.unread"
							class="absolute -right-1 -top-1 size-1.5 rounded-full bg-surface-red-5"
						/>
					</span>
				</template>
				<template #suffix>
					<Badge
						v-if="inbox.bell.unread"
						class="mr-2"
						:label="String(inbox.bell.unread)"
						variant="subtle"
						theme="red"
					/>
				</template>
			</SidebarItem>
		</template>
		<template #body="{ close }">
			<div
				class="ml-2 w-96 rounded-lg border border-outline-gray-2 bg-surface-white shadow-xl"
			>
				<div class="flex items-center border-b border-outline-gray-1 px-3 py-2">
					<span class="text-base font-medium text-ink-gray-9">Notifications</span>
					<Button
						v-if="inbox.bell.unread"
						class="ml-auto"
						size="sm"
						variant="ghost"
						label="Mark all read"
						@click="readAll"
					/>
				</div>
				<div class="max-h-[28rem] overflow-y-auto">
					<button
						v-for="n in inbox.bell.notifications"
						:key="n.name"
						class="flex w-full gap-2 border-b border-outline-gray-1 px-3 py-2 text-left last:border-0 hover:bg-surface-gray-1"
						@click="open(n, close)"
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
	</Popover>
</template>

<script setup>
import { useRouter } from "vue-router";
import { Badge, Button, Popover, SidebarItem, call } from "frappe-ui";
import LucideBell from "~icons/lucide/bell";
import { inbox, loadBell } from "@/stores/inbox";
import { fromNow } from "@/utils/format";

defineProps({ collapsed: { type: Boolean, default: false } });
const router = useRouter();
const kinds = { Assignment: "Assigned to you", Mention: "Mentioned you", Alert: "SLA alert" };

async function open(n, close) {
	if (!n.read) {
		await call("periskope_connector.api.notifications.mark_read", { name: n.name });
		loadBell();
	}
	close();
	router.push({ name: "Inbox", params: { chat: n.document_name } });
}

async function readAll() {
	await call("periskope_connector.api.notifications.mark_read");
	loadBell();
}
</script>
