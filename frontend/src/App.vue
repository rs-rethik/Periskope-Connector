<template>
	<FrappeUIProvider>
		<div class="safe-x flex h-full w-full overflow-hidden bg-surface-base">
			<FestivalLights />
			<AssistantPanel />
			<!-- no live updates (socket down): the app refreshes itself every 30 s meanwhile -->
			<div
				v-if="live.paused"
				class="fixed bottom-3 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full bg-surface-amber-2 px-3 py-1.5 text-xs font-medium text-ink-amber-8 shadow-md"
				role="status"
			>
				<LucideWifiOff class="size-3.5" />
				Live updates paused. Refreshing every 30 seconds.
			</div>
			<AppSidebar v-if="!isMobile" />
			<main class="app-canvas flex min-w-0 flex-1 flex-col">
				<div
					v-if="isMobile"
					class="safe-top box-content flex h-12 shrink-0 items-center gap-2 border-b border-outline-gray-1 px-2"
				>
					<Button
						class="tap"
						variant="ghost"
						tooltip="Menu"
						aria-label="Menu"
						@click="menuOpen = true"
					>
						<template #icon><LucideMenu class="size-5" /></template>
					</Button>
					<BrandLogo class="size-6" />
					<span class="truncate text-base font-medium text-ink-gray-9">{{
						boot.brand?.name || "WhatsApp"
					}}</span>
					<TestModeBadge />
					<button
						v-if="inbox.bell.unread"
						class="tap ml-auto mr-2 rounded-full bg-surface-red-5 px-1.5 text-xs text-ink-white"
						:aria-label="`${inbox.bell.unread} unread notifications`"
						@click="shell.notifications = true"
					>
						{{ inbox.bell.unread }}
					</button>
				</div>
				<div class="flex min-h-0 flex-1 flex-col">
					<router-view :key="$route.name" />
				</div>
			</main>

			<!-- the sidebar as a drawer on a phone; picking anything in it closes it -->
			<Transition
				enter-from-class="opacity-0"
				leave-to-class="opacity-0"
				enter-active-class="transition-opacity duration-200"
				leave-active-class="transition-opacity duration-200"
			>
				<div
					v-if="isMobile && menuOpen"
					role="dialog"
					aria-label="Menu"
					class="fixed inset-0 z-40 flex"
				>
					<div class="h-full shadow-2xl" @click="closeOnPick">
						<AppSidebar class="safe-top safe-bottom" mobile />
					</div>
					<div class="flex-1 bg-black/30" @click="menuOpen = false" />
				</div>
			</Transition>

			<!-- Settings and the phone's notifications live here, not in the sidebar: the drawer
			     closes (and unmounts) on a pick, and they must stay open -->
			<div
				v-if="isMobile && shell.notifications"
				role="dialog"
				aria-label="Notifications"
				class="safe-top safe-bottom fixed inset-0 z-40 flex flex-col bg-surface-base"
			>
				<NotificationsPanel
					class="min-h-0 flex-1"
					sheet
					@close="shell.notifications = false"
				/>
			</div>
			<SettingsModal v-if="boot.is_manager && shell.settingsUsed" v-model="shell.settings" />
		</div>
	</FrappeUIProvider>
</template>

<script setup>
import { defineAsyncComponent, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FrappeUIProvider, useTheme } from "frappe-ui";
import LucideMenu from "~icons/lucide/menu";
import AppSidebar from "@/components/AppSidebar.vue";
import BrandLogo from "@/components/BrandLogo.vue";
import FestivalLights from "@/components/FestivalLights.vue";
import NotificationsPanel from "@/components/NotificationsPanel.vue";
import TestModeBadge from "@/components/TestModeBadge.vue";
import LucideWifiOff from "~icons/lucide/wifi-off";
import { live } from "@/socket";
import { inbox, loadBell, loadCounts, loadViews } from "@/stores/inbox";
import { shell } from "@/stores/shell";
import { boot } from "@/boot";
import "@/utils/brand";
import { isMobile } from "@/utils/screen";

// Off the first download: the Assistant comes right after it, Settings when first opened.
const AssistantPanel = defineAsyncComponent(() => import("@/components/AssistantPanel.vue"));
const SettingsModal = defineAsyncComponent(() =>
	import("@/components/settings/SettingsModal.vue")
);

const menuOpen = ref(false);
useTheme(); // applies the saved light/dark choice before the sidebar mounts
const route = useRoute();
watch(
	() => route.fullPath,
	() => {
		menuOpen.value = false;
		shell.notifications = false;
	}
);

function closeOnPick(e) {
	if (e.target.closest('[data-slot="sidebar-item"]')) menuOpen.value = false;
}

// The sidebar's counts, saved views and the bell: loaded here, as a phone has no sidebar until the
// menu is opened and its header shows the unread badge.
onMounted(() => {
	loadCounts();
	loadViews();
	loadBell();
});
</script>
