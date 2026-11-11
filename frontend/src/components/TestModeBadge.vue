<template>
	<Tooltip
		v-if="boot.test_mode"
		text="Test mode: replies go only to test numbers and emails only to test addresses. Nothing reaches real customers."
	>
		<span
			class="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-amber-2 font-medium text-ink-amber-8"
			:class="dot ? 'size-2.5 justify-center p-0' : 'px-1.5 py-0.5 text-xs'"
			:aria-label="dot ? 'Test mode' : undefined"
		>
			<template v-if="!dot">
				<LucideFlaskConical class="size-3" />
				Test mode
			</template>
			<span v-else class="size-2.5 rounded-full bg-surface-amber-5" />
		</span>
	</Tooltip>
</template>

<script setup>
import { Tooltip } from "frappe-ui";
import LucideFlaskConical from "~icons/lucide/flask-conical";
import { boot } from "@/boot";

// Shown beside the app's name while test mode is on (boot.test_mode; Settings > Test mode).
// dot: a small amber dot, for the collapsed sidebar.
defineProps({ dot: { type: Boolean, default: false } });
</script>
