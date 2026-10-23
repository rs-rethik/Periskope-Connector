import { createRouter, createWebHistory } from "vue-router";

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
	{ path: "/:pathMatch(.*)*", redirect: { name: "Inbox" } },
];

export default createRouter({
	history: createWebHistory("/whatsapp/"),
	routes,
});
