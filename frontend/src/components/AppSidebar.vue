<template>
	<div class="relative flex h-full bg-surface-gray-1">
		<Sidebar v-model:collapsed="collapsed" class="border-r border-outline-gray-1">
			<div class="flex h-full flex-col p-2">
				<div class="flex items-center gap-2 px-2 py-1.5">
					<img :src="logo" class="size-7 shrink-0 rounded" alt="" />
					<div v-if="!collapsed" class="min-w-0">
						<div class="truncate text-base font-medium text-ink-gray-9">WhatsApp</div>
						<div class="truncate text-xs text-ink-gray-5">{{ boot.full_name }}</div>
					</div>
				</div>

				<div class="-mx-2 mt-3 flex flex-1 flex-col gap-0.5 overflow-y-auto px-2">
					<SidebarItem
						label="Inbox"
						:to="{ name: 'Inbox' }"
						:active="$route.name === 'Inbox'"
					>
						<template #prefix
							><LucideMessagesSquare class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<SidebarItem
						label="Conversations"
						:to="{ name: 'Conversations' }"
						:active="['Conversations', 'Conversation'].includes($route.name)"
					>
						<template #prefix><LucideList class="size-4 text-ink-gray-6" /></template>
					</SidebarItem>

					<SidebarLabel label="Views" class="mt-3" divider />
					<SidebarItem
						v-for="f in QUICK_FILTERS"
						:key="f.key"
						:label="f.label"
						:active="inbox.quick === f.key"
						@click="inbox.quick = f.key"
					>
						<template #prefix
							><component :is="filterIcons[f.key]" class="size-4 text-ink-gray-6"
						/></template>
						<template #suffix>
							<span v-if="inbox.counts[f.key]" class="mr-2 text-xs text-ink-gray-5">
								{{ inbox.counts[f.key] }}
							</span>
						</template>
					</SidebarItem>

					<SidebarLabel label="Numbers" class="mt-3" divider />
					<SidebarItem
						label="All numbers"
						:active="!inbox.number"
						@click="inbox.number = ''"
					>
						<template #prefix><LucidePhone class="size-4 text-ink-gray-6" /></template>
					</SidebarItem>
					<SidebarItem
						v-for="n in boot.numbers"
						:key="n.name"
						:label="n.label"
						:active="inbox.number === n.name"
						@click="inbox.number = n.name"
					>
						<template #prefix>
							<span
								class="size-2 rounded-full"
								:class="n.is_ready ? 'bg-surface-green-3' : 'bg-surface-red-5'"
								:title="n.is_ready ? 'Connected' : n.wa_state || 'Not connected'"
							/>
						</template>
						<template #suffix>
							<Badge
								v-if="unreadOn(n.name)"
								class="mr-2"
								:label="String(unreadOn(n.name))"
								variant="subtle"
								theme="green"
							/>
						</template>
					</SidebarItem>
					<div
						v-if="!boot.numbers.length && !collapsed"
						class="px-2 py-1 text-xs text-ink-gray-5"
					>
						No WhatsApp number is open to you yet. Ask a manager to add you to a
						number.
					</div>
				</div>

				<div class="mt-auto pt-2">
					<SidebarItem label="Open CRM" to="/crm" @click.prevent="goTo('/crm')">
						<template #prefix
							><LucideExternalLink class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<SidebarCollapseToggle />
				</div>
			</div>
		</Sidebar>
	</div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { Badge, Sidebar, SidebarCollapseToggle, SidebarItem, SidebarLabel } from "frappe-ui";
import LucideMessagesSquare from "~icons/lucide/messages-square";
import LucideList from "~icons/lucide/list";
import LucidePhone from "~icons/lucide/phone";
import LucideExternalLink from "~icons/lucide/external-link";
import LucideInbox from "~icons/lucide/inbox";
import LucideUser from "~icons/lucide/user";
import LucideUserX from "~icons/lucide/user-x";
import LucideMessageCircleQuestion from "~icons/lucide/message-circle-question";
import LucideMail from "~icons/lucide/mail";
import { boot } from "@/boot";
import { QUICK_FILTERS, inbox, loadCounts } from "@/stores/inbox";

const logo = "/assets/periskope_connector/images/logo.svg";
const collapsed = ref(false);
const filterIcons = {
	all: LucideInbox,
	mine: LucideUser,
	unassigned: LucideUserX,
	unanswered: LucideMessageCircleQuestion,
	unread: LucideMail,
};

function unreadOn(number) {
	return (inbox.counts.unread_by_number || {})[number] || 0;
}

function goTo(url) {
	window.location.href = url;
}

onMounted(loadCounts);
</script>
