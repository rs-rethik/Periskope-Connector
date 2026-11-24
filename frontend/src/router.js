import { createRouter, createWebHistory } from "vue-router";
import { boot } from "@/boot";

const routes = [
	{ path: "/", redirect: { name: "Today" } },
	{
		path: "/inbox/:chat?",
		name: "Inbox",
		component: () => import("@/pages/Inbox.vue"),
	},
	{
		path: "/today",
		name: "Today",
		component: () => import("@/pages/Today.vue"),
	},
	{
		path: "/conversations",
		name: "Conversations",
		component: () => import("@/pages/Conversations.vue"),
	},
	{
		path: "/conversations/:chat",
		name: "Conversation",
		component: () => import("@/pages/Conversation.vue"),
	},
	{
		path: "/dashboard",
		name: "Dashboard",
		component: () => import("@/pages/Dashboard.vue"),
		beforeEnter: () => (boot.is_manager ? true : { name: "Inbox" }),
	},
	...[
		["/health", "Health", "Health", true],
		["/flow", "Flow", "Flow", true],
		["/data/:kind", "DataList", "DataList"],
		["/knowledge", "Knowledge", "Knowledge"],
		["/scheduled", "Scheduled", "Scheduled"],
		["/broadcasts", "Broadcasts", "Broadcasts"],
		["/reports", "Reports", "Reports"],
	].map(([path, name, page, everyNumber]) => ({
		path,
		name,
		component: () => import(`@/pages/${page}.vue`),
		// health and flow cover every number: not for a manager limited to numbers
		beforeEnter: () =>
			boot.is_manager && (!everyNumber || boot.sees_every_number) ? true : { name: "Inbox" },
	})),
	{ path: "/:pathMatch(.*)*", redirect: { name: "Today" } },
];

export default createRouter({
	history: createWebHistory("/whatsapp/"),
	routes,
});
