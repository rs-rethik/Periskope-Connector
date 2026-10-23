<template>
	<FrappeUIProvider>
		<div class="flex h-full w-full overflow-hidden bg-surface-white">
			<AppSidebar />
			<main class="flex min-w-0 flex-1 flex-col">
				<router-view :key="$route.name" />
			</main>
		</div>
	</FrappeUIProvider>
</template>

<script setup>
import { FrappeUIProvider } from "frappe-ui";
import AppSidebar from "@/components/AppSidebar.vue";
</script>
