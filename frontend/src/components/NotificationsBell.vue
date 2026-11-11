<template>
	<!-- full width, like the other rows, so the panel opens beside the sidebar, not over it -->
	<Popover class="w-full" placement="right-start">
		<template #target="{ togglePopover }">
			<!-- a phone has no room beside the drawer: App.vue shows the list as a sheet -->
			<SidebarItem
				class="w-full"
				label="Notifications"
				@click="isMobile ? (shell.notifications = true) : togglePopover()"
			>
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
			<NotificationsPanel
				class="ml-2 w-96 rounded-lg border border-outline-gray-2 bg-surface-base shadow-xl"
				@close="close"
			/>
		</template>
	</Popover>
</template>

<script setup>
import { Badge, Popover, SidebarItem } from "frappe-ui";
import LucideBell from "~icons/lucide/bell";
import NotificationsPanel from "@/components/NotificationsPanel.vue";
import { inbox } from "@/stores/inbox";
import { shell } from "@/stores/shell";
import { isMobile } from "@/utils/screen";

defineProps({ collapsed: { type: Boolean, default: false } });
</script>
