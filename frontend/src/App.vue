<template>
	<FrappeUIProvider>
		<div class="flex h-full w-full overflow-hidden bg-surface-base">
			<FestivalLights />
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
					class="flex h-12 shrink-0 items-center gap-2 border-b border-outline-gray-1 px-2"
				>
					<Button
						variant="ghost"
						tooltip="Menu"
						aria-label="Menu"
						@click="menuOpen = true"
					>
						<template #icon><LucideMenu class="size-5" /></template>
					</Button>
					<img
						:src="boot.brand?.logo || logo"
						class="size-6 rounded object-contain"
						alt=""
					/>
					<span class="truncate text-base font-medium text-ink-gray-9">{{
						boot.brand?.name || "WhatsApp"
					}}</span>
					<span
						v-if="inbox.bell.unread"
						class="ml-auto mr-2 rounded-full bg-surface-red-5 px-1.5 text-xs text-ink-white"
						>{{ inbox.bell.unread }}</span
					>
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
				<div v-if="isMobile && menuOpen" class="fixed inset-0 z-40 flex">
					<div class="h-full shadow-2xl" @click="closeOnPick">
						<AppSidebar mobile />
					</div>
					<div class="flex-1 bg-black/30" @click="menuOpen = false" />
				</div>
			</Transition>
		</div>
	</FrappeUIProvider>
</template>

<script setup>
import { ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, FrappeUIProvider, useTheme } from "frappe-ui";
import LucideMenu from "~icons/lucide/menu";
import AppSidebar from "@/components/AppSidebar.vue";
import FestivalLights from "@/components/FestivalLights.vue";
import LucideWifiOff from "~icons/lucide/wifi-off";
import { live } from "@/socket";
import { inbox } from "@/stores/inbox";
import { boot } from "@/boot";
import "@/utils/brand";
import { isMobile } from "@/utils/screen";

const logo = "/assets/periskope_connector/images/logo.svg";
const menuOpen = ref(false);
useTheme(); // applies the saved light/dark choice before the sidebar mounts
const route = useRoute();
watch(
	() => route.fullPath,
	() => (menuOpen.value = false)
);

function closeOnPick(e) {
	if (e.target.closest('[data-slot="sidebar-item"]')) menuOpen.value = false;
}
</script>
