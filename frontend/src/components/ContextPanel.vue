<template>
	<div
		class="flex h-full flex-col overflow-y-auto border-l border-outline-gray-1 bg-surface-white"
	>
		<div v-if="chat" class="flex flex-col gap-4 p-4">
			<div>
				<div class="text-xs font-medium uppercase text-ink-gray-5">Contact</div>
				<div class="mt-1 text-base text-ink-gray-9">{{ chat.title }}</div>
				<div v-if="chat.contact_number" class="text-sm text-ink-gray-6">
					+{{ chat.contact_number }}
				</div>
				<div v-if="chat.is_group" class="text-sm text-ink-gray-6">WhatsApp group</div>
			</div>

			<div>
				<div class="text-xs font-medium uppercase text-ink-gray-5">Our number</div>
				<div class="mt-1 text-sm text-ink-gray-8">{{ chat.number_label }}</div>
			</div>

			<div>
				<div class="text-xs font-medium uppercase text-ink-gray-5">CRM</div>
				<a
					v-if="chat.crm_deal"
					:href="`/crm/deals/${chat.crm_deal}`"
					class="mt-1 flex items-center gap-1 text-sm text-ink-blue-link hover:underline"
				>
					Deal {{ chat.deal_title || chat.crm_deal }}
					<LucideExternalLink class="size-3" />
				</a>
				<a
					v-if="chat.crm_lead"
					:href="`/crm/leads/${chat.crm_lead}`"
					class="mt-1 flex items-center gap-1 text-sm text-ink-blue-link hover:underline"
				>
					Lead {{ chat.lead_title || chat.crm_lead }}
					<LucideExternalLink class="size-3" />
				</a>
				<div v-if="!chat.crm_deal && !chat.crm_lead" class="mt-1 text-sm text-ink-gray-5">
					Not linked to a lead or deal.
				</div>
			</div>

			<div v-if="chat.ai_tag">
				<div class="text-xs font-medium uppercase text-ink-gray-5">AI tag</div>
				<Badge class="mt-1" :label="chat.ai_tag" variant="subtle" theme="blue" />
			</div>

			<div>
				<div class="text-xs font-medium uppercase text-ink-gray-5">Assigned to</div>
				<div class="mt-1 text-sm text-ink-gray-8">
					{{ chat.assigned_to?.length ? chat.assigned_to.join(", ") : "Nobody" }}
				</div>
			</div>

			<div class="text-xs text-ink-gray-5">{{ chat.message_count }} messages</div>
		</div>
	</div>
</template>

<script setup>
import { Badge } from "frappe-ui";
import LucideExternalLink from "~icons/lucide/external-link";

defineProps({ chat: { type: Object, default: null } });
</script>
