<template>
	<Dialog v-model="open" :options="{ size: '6xl' }">
		<template #body>
			<div class="flex h-[80vh] overflow-hidden rounded-xl bg-surface-white">
				<!-- Menu -->
				<nav
					class="flex w-56 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-outline-gray-1 bg-surface-gray-1 p-2"
				>
					<div class="px-2 pb-2 pt-1 text-base font-semibold text-ink-gray-9">
						Settings
					</div>
					<template v-for="group in menu" :key="group.label">
						<div class="px-2 pb-1 pt-3 text-xs font-medium uppercase text-ink-gray-5">
							{{ group.label }}
						</div>
						<button
							v-for="item in group.items"
							:key="item.key"
							class="flex items-center gap-2 rounded px-2 py-1.5 text-left text-sm"
							:class="
								page === item.key
									? 'bg-surface-white text-ink-gray-9 shadow-sm'
									: 'text-ink-gray-7 hover:bg-surface-gray-2'
							"
							@click="page = item.key"
						>
							<component :is="item.icon" class="size-4 text-ink-gray-6" />
							{{ item.label }}
						</button>
					</template>
				</nav>

				<!-- Page -->
				<div class="min-w-0 flex-1">
					<RecordsPage
						v-if="page === 'teams'"
						kind="teams"
						title="Teams"
						singular="Team"
						name-field="team_name"
						description="Who answers which numbers, and how new chats are handed out (round robin, by load, or by hand)."
						empty="No team yet. A team takes the chats of its numbers and gives them to its members."
						@saved="refreshInbox"
					/>
					<RecordsPage
						v-else-if="page === 'numbers'"
						kind="numbers"
						title="WhatsApp numbers"
						singular="Number"
						description="Every number connected in Periskope, with its team, its rep and its connection."
						empty="No number yet. Sync the numbers from Periskope."
						@saved="refreshInbox"
					>
						<template v-if="my.admin" #actions="{ reload }">
							<Button
								label="Sync from Periskope"
								:loading="syncing"
								@click="syncNumbers(reload)"
							/>
						</template>
						<template #cell="{ row, field }">
							<template v-if="field.fieldname === 'wa_state'">
								<span class="inline-flex items-center gap-1.5">
									<span
										class="size-2 rounded-full"
										:class="
											row.is_ready
												? 'bg-surface-green-3'
												: 'bg-surface-red-5'
										"
									/>
									{{ row.wa_state || (row.is_ready ? "CONNECTED" : "Unknown") }}
								</span>
							</template>
							<template v-else-if="field.fieldtype === 'Check'">{{
								row[field.fieldname] ? "Yes" : "No"
							}}</template>
							<template v-else-if="field.fieldtype === 'Datetime'">{{
								row[field.fieldname] ? fromNow(row[field.fieldname]) : ""
							}}</template>
							<template v-else>{{ row[field.fieldname] }}</template>
						</template>
					</RecordsPage>
					<RecordsPage
						v-else-if="page === 'agents'"
						kind="agents"
						title="Agents"
						singular="Agent"
						name-field="user"
						description="The sales users who work WhatsApp chats, and the numbers each may use. Team members get their team's numbers too."
						empty="No agent yet."
						@saved="refreshInbox"
					>
						<template #actions="{ reload }">
							<Button label="Invite a new user" @click="openInvite(reload)" />
						</template>
					</RecordsPage>
					<RecordsPage
						v-else-if="page === 'sla'"
						kind="sla"
						title="SLA policies"
						singular="Policy"
						name-field="policy_name"
						description="How soon a customer must get an answer, and a chat be resolved. A policy covers its numbers and teams; the default covers the rest."
						empty="No policy yet: chats have no SLA."
					/>
					<RecordsPage
						v-else-if="page === 'quick_replies'"
						kind="quick_replies"
						title="Quick replies"
						singular="Quick reply"
						description="Everyone's quick replies. Shared ones are offered to everybody in the composer (type /)."
					/>
					<RecordsPage
						v-else-if="page === 'labels'"
						kind="labels"
						title="Labels and AI tags"
						singular="Label"
						name-field="tag_name"
						description="The AI tags each customer message with one of these; reps put them on chats as labels. Switch one off instead of deleting it."
						@saved="refreshInbox"
					/>
					<SettingsSection
						v-else-if="sectionKeys.includes(page)"
						:section-key="page"
						:data="settings"
						@saved="settingsSaved"
					/>
				</div>
			</div>
		</template>
	</Dialog>

	<Dialog v-model="inviteOpen" :options="{ title: 'Invite a new user' }">
		<template #body-content>
			<div class="flex flex-col gap-3">
				<FormControl v-model="invite.email" label="Email" />
				<FormControl v-model="invite.first_name" label="Name" />
				<div>
					<label class="mb-1 block text-xs text-ink-gray-5">WhatsApp numbers</label>
					<Picker v-model="invite.numbers" multiple doctype="WhatsApp Number" />
				</div>
				<FormControl
					v-model="invite.send_welcome_email"
					type="checkbox"
					label="Email them a welcome link to set a password"
				/>
				<p class="text-xs text-ink-gray-5">
					They get the Sales User role: the CRM and this inbox, without Desk.
				</p>
				<ErrorMessage :message="inviteError" />
			</div>
		</template>
		<template #actions>
			<Button variant="solid" label="Invite" :disabled="!invite.email" @click="sendInvite" />
		</template>
	</Dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { Button, Dialog, ErrorMessage, FormControl, call, toast } from "frappe-ui";
import LucideUsersRound from "~icons/lucide/users-round";
import LucidePhone from "~icons/lucide/phone";
import LucideUserCog from "~icons/lucide/user-cog";
import LucideTimer from "~icons/lucide/timer";
import LucideZap from "~icons/lucide/zap";
import LucideTag from "~icons/lucide/tag";
import LucidePlug from "~icons/lucide/plug";
import LucideBot from "~icons/lucide/bot";
import LucideMic from "~icons/lucide/mic";
import LucideBellRing from "~icons/lucide/bell-ring";
import LucideClock from "~icons/lucide/clock";
import LucideFlaskConical from "~icons/lucide/flask-conical";
import RecordsPage from "@/components/settings/RecordsPage.vue";
import SettingsSection from "@/components/settings/SettingsSection.vue";
import Picker from "@/components/settings/Picker.vue";
import { boot } from "@/boot";
import { loadCounts, loadTeams } from "@/stores/inbox";
import { fromNow } from "@/utils/format";

const open = defineModel({ type: Boolean, default: false });
const page = ref("teams");
const my = ref({ manager: boot.is_manager, admin: boot.is_admin });
const settings = ref(null);
const syncing = ref(false);
const inviteOpen = ref(false);
const invite = ref({ email: "", first_name: "", numbers: [], send_welcome_email: false });
const inviteError = ref("");
let reloadAgents = null;

const sectionKeys = ["connect", "ai", "voice", "team", "auto_reply", "test_mode"];
const menu = computed(() => [
	{
		label: "Team",
		items: [
			{ key: "teams", label: "Teams", icon: LucideUsersRound },
			{ key: "agents", label: "Agents", icon: LucideUserCog },
			{ key: "numbers", label: "WhatsApp numbers", icon: LucidePhone },
		],
	},
	{
		label: "Work",
		items: [
			{ key: "sla", label: "SLA policies", icon: LucideTimer },
			{ key: "quick_replies", label: "Quick replies", icon: LucideZap },
			...(my.value.admin
				? [{ key: "labels", label: "Labels and AI tags", icon: LucideTag }]
				: []),
		],
	},
	...(my.value.admin
		? [
				{
					label: "System",
					items: [
						{ key: "connect", label: "Periskope connection", icon: LucidePlug },
						{ key: "ai", label: "AI", icon: LucideBot },
						{ key: "voice", label: "Voice notes", icon: LucideMic },
						{ key: "team", label: "Approvers and alerts", icon: LucideBellRing },
						{ key: "auto_reply", label: "Auto reply and hours", icon: LucideClock },
						{ key: "test_mode", label: "Test mode", icon: LucideFlaskConical },
					],
				},
		  ]
		: []),
]);

async function loadSettings() {
	if (my.value.admin) settings.value = await call("periskope_connector.api.admin.get_settings");
}

function settingsSaved(data) {
	if (data) settings.value = data;
	else loadSettings();
}

function refreshInbox() {
	loadTeams();
	loadCounts();
}

async function syncNumbers(reload) {
	syncing.value = true;
	try {
		await call("periskope_connector.api.admin.run_action", { action: "sync_numbers" });
		toast.success("Numbers synced from Periskope");
		reload();
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "Could not sync.");
	} finally {
		syncing.value = false;
	}
}

function openInvite(reload) {
	reloadAgents = reload;
	inviteError.value = "";
	invite.value = { email: "", first_name: "", numbers: [], send_welcome_email: false };
	inviteOpen.value = true;
}

async function sendInvite() {
	inviteError.value = "";
	try {
		await call("periskope_connector.api.admin.invite_agent", {
			...invite.value,
			send_welcome_email: invite.value.send_welcome_email ? 1 : 0,
		});
		toast.success("Agent added");
		inviteOpen.value = false;
		reloadAgents?.();
	} catch (e) {
		inviteError.value = e?.messages?.[0] || e?.message || "Not added.";
	}
}

watch(open, async (o) => {
	if (!o) return;
	my.value = await call("periskope_connector.api.admin.my_access");
	loadSettings();
});
watch(page, (p) => sectionKeys.includes(p) && !settings.value && loadSettings());
</script>
