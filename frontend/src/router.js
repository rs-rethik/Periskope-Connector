import { createRouter, createWebHistory } from "vue-router";
import { boot } from "@/boot";

const routes = [
	{ path: "/", redirect: { name: "Inbox" } },
	{
		path: "/inbox/:chat?",
		name: "Inbox",
		component: () => import("@/pages/Inbox.vue"),
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
		["/health", "Health", "Health"],
		["/flow", "Flow", "Flow"],
		["/reports/:report?", "Reports", "Reports"],
		["/data/:kind", "DataList", "DataList"],
		["/knowledge", "Knowledge", "Knowledge"],
	].map(([path, name, page]) => ({
		path,
		name,
		component: () => import(`@/pages/${page}.vue`),
		beforeEnter: () => (boot.is_manager ? true : { name: "Inbox" }),
	})),
	{ path: "/:pathMatch(.*)*", redirect: { name: "Inbox" } },
];

export default createRouter({
	history: createWebHistory("/whatsapp/"),
	routes,
});
