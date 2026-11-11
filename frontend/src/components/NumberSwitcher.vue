<template>
	<!-- which WhatsApp number the inbox shows: all of them, or one -->
	<Dropdown :options="options" placement="right">
		<button
			class="flex items-center gap-2 border border-outline-gray-1 bg-surface-base text-left shadow-sm transition hover:border-outline-gray-2"
			:class="
				compact
					? 'rounded-full py-1 pl-1 pr-2.5'
					: 'h-full min-h-[44px] rounded-xl px-3 py-1.5'
			"
			aria-label="Switch WhatsApp number"
		>
			<span
				class="grid size-7 shrink-0 place-items-center rounded-lg bg-surface-green-1 text-ink-green-7"
				:class="compact ? 'size-6' : ''"
			>
				<LucidePhone class="size-3.5" />
			</span>
			<span class="min-w-0">
				<span v-if="!compact" class="block text-xs leading-tight text-ink-gray-5"
					>Number</span
				>
				<span class="flex items-center gap-1.5 text-sm font-medium text-ink-gray-9">
					<span
						v-if="current"
						class="size-2 shrink-0 rounded-full"
						:class="current.is_ready ? 'bg-surface-green-6' : 'bg-surface-red-6'"
					/>
					<span class="max-w-[10rem] truncate">{{
						current ? current.label : "All numbers"
					}}</span>
				</span>
			</span>
			<LucideChevronsUpDown class="size-4 shrink-0 text-ink-gray-5" />
		</button>
	</Dropdown>
</template>

<script setup>
import { computed, h } from "vue";
import { Dropdown } from "frappe-ui";
import LucidePhone from "~icons/lucide/phone";
import LucideChevronsUpDown from "~icons/lucide/chevrons-up-down";
import { boot } from "@/boot";
import { inbox } from "@/stores/inbox";

defineProps({ compact: { type: Boolean, default: false } });

const current = computed(() => boot.numbers.find((n) => n.name === inbox.number) || null);

function unread(number) {
	return (inbox.counts.unread_by_number || {})[number] || 0;
}

function dot(ready) {
	return () =>
		h("span", {
			class: `ml-0.5 size-2 rounded-full ${
				ready ? "bg-surface-green-6" : "bg-surface-red-6"
			}`,
		});
}

const options = computed(() => [
	{
		label: "All numbers",
		icon: () => h(LucidePhone, { class: "size-4 text-ink-gray-6" }),
		onClick: () => (inbox.number = ""),
	},
	...boot.numbers.map((n) => ({
		label: `${n.label}${n.source === "Meta" ? " · Meta" : ""}${
			unread(n.name) ? ` (${unread(n.name)} unread)` : ""
		}${n.is_ready ? "" : " · not connected"}`,
		icon: dot(n.is_ready),
		onClick: () => (inbox.number = n.name),
	})),
]);
</script>
