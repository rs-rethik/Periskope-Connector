<template>
	<div class="side-panel relative flex h-full">
		<Sidebar v-model:collapsed="collapsed" class="border-r border-outline-gray-1">
			<div class="flex h-full flex-col p-2">
				<div class="flex shrink-0 items-center gap-2 px-2 py-1.5">
					<span class="relative shrink-0">
						<BrandLogo class="size-7" />
						<span v-if="collapsed" class="absolute -right-1 -top-1 flex">
							<TestModeBadge dot />
						</span>
					</span>
					<div v-if="!collapsed" class="min-w-0">
						<div class="truncate text-base font-medium text-ink-gray-9">
							{{ boot.brand?.name || "WhatsApp" }}
						</div>
						<div class="flex min-w-0 items-center gap-1.5">
							<TestModeBadge />
							<span class="truncate text-xs text-ink-gray-5">{{
								boot.full_name
							}}</span>
						</div>
					</div>
				</div>

				<!-- rows keep their height; a long list scrolls instead of squeezing them -->
				<div
					class="-mx-2 mt-3 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 [&>*]:shrink-0"
				>
					<!-- back to Sales One, the sales app this inbox opens from -->
					<SidebarItem
						v-if="boot.sales_one"
						label="Back to Sales One"
						to="/sales/"
						@click.prevent="backToSalesOne($event)"
					>
						<template #prefix
							><LucideArrowLeft class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<NotificationsBell :collapsed="collapsed" />
					<SidebarItem
						label="Today"
						:to="{ name: 'Today' }"
						:active="$route.name === 'Today'"
					>
						<template #prefix
							><LucideSunrise class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
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

					<SidebarItem
						v-if="boot.is_manager"
						label="Dashboard"
						:to="{ name: 'Dashboard' }"
						:active="$route.name === 'Dashboard'"
					>
						<template #prefix
							><LucideChartColumn class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>

					<SidebarLabel class="mt-3" divider
						><span class="text-sm">Views</span></SidebarLabel
					>
					<SidebarItem
						v-for="f in QUICK_FILTERS"
						:key="f.key"
						:label="f.label"
						:active="onList && inbox.quick === f.key"
						:suffix="inbox.counts[f.key] ? String(inbox.counts[f.key]) : ''"
						@click="pick({ quick: f.key })"
					>
						<template #prefix
							><component :is="filterIcons[f.key]" class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>

					<template v-if="inbox.views.length">
						<SidebarLabel class="mt-3" divider
							><span class="text-sm">Saved views</span></SidebarLabel
						>
						<SidebarItem
							v-for="v in inbox.views"
							:key="v.name"
							:label="v.label"
							:to="{ name: 'Conversations', query: { view: v.name } }"
							:active="$route.query.view === v.name"
						>
							<template #prefix
								><component
									:is="v.is_shared ? LucideBookmarkCheck : LucideBookmark"
									class="size-4 text-ink-gray-6"
							/></template>
						</SidebarItem>
					</template>

					<template v-if="boot.is_manager">
						<SidebarLabel class="mt-3" divider
							><span class="text-sm">Manage</span></SidebarLabel
						>
						<SidebarItem
							v-for="m in manage.filter(
								(x) => !x.everyNumber || boot.sees_every_number
							)"
							:key="m.label"
							:label="m.label"
							:to="m.to"
							:active="m.active($route)"
						>
							<template #prefix
								><component :is="m.icon" class="size-4 text-ink-gray-6"
							/></template>
						</SidebarItem>
					</template>

					<div
						v-if="!boot.numbers.length && !collapsed"
						class="px-2 py-1 text-xs text-ink-gray-5"
					>
						No WhatsApp number is open to you yet. Ask a manager to add you to a
						number.
					</div>
				</div>

				<div class="mt-auto shrink-0 border-t border-outline-gray-1 pt-2">
					<!-- a phone hides the floating Assistant button where it would cover the page -->
					<SidebarItem v-if="mobile" label="Assistant" @click="assistant.open = true">
						<template #prefix
							><LucideMessageCircleQuestion class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<SidebarItem v-if="boot.is_manager" label="Settings" @click="openSettings">
						<template #prefix
							><LucideSettings class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<SidebarItem
						:label="currentTheme === 'dark' ? 'Light mode' : 'Dark mode'"
						@click="toggleTheme()"
					>
						<template #prefix
							><component
								:is="currentTheme === 'dark' ? LucideSun : LucideMoon"
								class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<SidebarItem label="Open CRM" to="/crm" @click.prevent="goTo('/crm')">
						<template #prefix
							><LucideExternalLink class="size-4 text-ink-gray-6"
						/></template>
					</SidebarItem>
					<SidebarCollapseToggle v-if="!mobile" />
				</div>
			</div>
		</Sidebar>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Sidebar, SidebarCollapseToggle, SidebarItem, SidebarLabel, useTheme } from "frappe-ui";
import LucideMoon from "~icons/lucide/moon";
import LucideSun from "~icons/lucide/sun";
import LucideMessagesSquare from "~icons/lucide/messages-square";
import LucideSunrise from "~icons/lucide/sunrise";
import LucideList from "~icons/lucide/list";
import LucideExternalLink from "~icons/lucide/external-link";
import LucideArrowLeft from "~icons/lucide/arrow-left";
import LucideInbox from "~icons/lucide/inbox";
import LucideUser from "~icons/lucide/user";
import LucideUserX from "~icons/lucide/user-x";
import LucideMessageCircleQuestion from "~icons/lucide/message-circle-question";
import LucideMail from "~icons/lucide/mail";
import LucideTimerOff from "~icons/lucide/timer-off";
import LucideFileChartColumn from "~icons/lucide/file-chart-column";
import LucideLink2Off from "~icons/lucide/link-2-off";
import LucideUsersRound from "~icons/lucide/users-round";
import LucideBookmark from "~icons/lucide/bookmark";
import LucideBookmarkCheck from "~icons/lucide/bookmark-check";
import TestModeBadge from "@/components/TestModeBadge.vue";
import NotificationsBell from "@/components/NotificationsBell.vue";
import LucideSettings from "~icons/lucide/settings";
import LucideChartColumn from "~icons/lucide/chart-column";
import LucideUserSearch from "~icons/lucide/user-search";
import LucideUsers from "~icons/lucide/users";
import LucideLightbulb from "~icons/lucide/lightbulb";
import LucideBookOpen from "~icons/lucide/book-open";
import LucideCalendarClock from "~icons/lucide/calendar-clock";
import LucideMegaphone from "~icons/lucide/megaphone";
import LucideActivity from "~icons/lucide/activity";
import LucideScrollText from "~icons/lucide/scroll-text";
import { boot } from "@/boot";
import BrandLogo from "@/components/BrandLogo.vue";
import { assistant } from "@/stores/assistant";
import { QUICK_FILTERS, inbox } from "@/stores/inbox";
import { openSettings } from "@/stores/shell";
import { backToSalesOne } from "@/stores/salesOne";

defineProps({ mobile: { type: Boolean, default: false } });
const collapsed = ref(false);
const { currentTheme, toggleTheme } = useTheme();
const manage = [
	{
		label: "Contact queue",
		icon: LucideUserSearch,
		to: { name: "DataList", params: { kind: "contacts" } },
		active: (r) => r.params.kind === "contacts",
	},
	{
		label: "Groups",
		icon: LucideUsers,
		to: { name: "DataList", params: { kind: "groups" } },
		active: (r) => r.params.kind === "groups",
	},
	{
		label: "Suggestions",
		everyNumber: true, // changes the whole app: managers who see every number
		icon: LucideLightbulb,
		to: { name: "DataList", params: { kind: "suggestions" } },
		active: (r) => r.params.kind === "suggestions",
	},
	{
		label: "Scheduled",
		icon: LucideCalendarClock,
		to: { name: "Scheduled" },
		active: (r) => r.name === "Scheduled",
	},
	{
		label: "Broadcasts",
		icon: LucideMegaphone,
		to: { name: "Broadcasts" },
		active: (r) => r.name === "Broadcasts",
	},
	{
		label: "Knowledge base",
		icon: LucideBookOpen,
		to: { name: "Knowledge" },
		active: (r) => r.name === "Knowledge",
	},
	{
		label: "Reports",
		icon: LucideFileChartColumn,
		to: { name: "Reports" },
		active: (r) => r.name === "Reports",
	},
	{
		label: "Health and flow",
		everyNumber: true,
		icon: LucideActivity,
		to: { name: "Health" },
		active: (r) => ["Health", "Flow"].includes(r.name),
	},
	{
		label: "Logs",
		icon: LucideScrollText,
		to: { name: "DataList", params: { kind: "agent_actions" } },
		active: (r) =>
			[
				"agent_actions",
				"agent_runs",
				"ai_usage",
				"webhook_logs",
				"auto_replies",
				"reminders",
			].includes(r.params.kind),
	},
];
const filterIcons = {
	all: LucideInbox,
	mine: LucideUser,
	unassigned: LucideUserX,
	unanswered: LucideMessageCircleQuestion,
	unread: LucideMail,
	breached: LucideTimerOff,
	my_team: LucideUsersRound,
	review: LucideLink2Off,
};

// Views and numbers filter the chat list, so on any other page (Health, Flow, ...) a click opens
// the inbox with that filter; they show as selected only where the list is.
const route = useRoute();
const router = useRouter();
const onList = computed(() => ["Inbox", "Conversations"].includes(route.name));

function pick(choice) {
	Object.assign(inbox, choice);
	if (!onList.value) router.push({ name: "Inbox" });
}

function goTo(url) {
	window.location.href = url;
}
</script>
