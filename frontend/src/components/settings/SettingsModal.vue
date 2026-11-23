<template>
	<!-- unsaved changes: Esc and outside clicks do not close it (Cancel or Save first) -->
	<Dialog v-model="open" :options="{ size: '6xl' }" :dismissible="!dirty">
		<template #body>
			<!-- a phone: the whole screen (theme.css), the menu as a select above the page -->
			<div
				class="settings-sheet flex overflow-hidden bg-surface-base"
				:class="
					isMobile
						? 'safe-top safe-bottom h-[100dvh] flex-col'
						: 'h-[min(46rem,90vh)] rounded-xl'
				"
			>
				<div
					v-if="isMobile"
					class="flex shrink-0 items-center gap-2 border-b border-outline-gray-1 bg-surface-gray-1 px-3 py-2"
				>
					<select
						class="h-10 min-w-0 flex-1 rounded border border-outline-gray-2 bg-surface-base py-0 pl-3 text-[16px] font-medium text-ink-gray-9"
						aria-label="Settings section"
						:value="page"
						@change="pick"
					>
						<optgroup v-for="group in menu" :key="group.label" :label="group.label">
							<option v-for="item in group.items" :key="item.key" :value="item.key">
								{{ item.label }}
							</option>
						</optgroup>
					</select>
					<Button
						class="tap"
						variant="ghost"
						aria-label="Close the settings"
						@click="close"
					>
						<template #icon><LucideX class="size-4" /></template>
					</Button>
				</div>
				<!-- Menu -->
				<nav
					v-else
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
							class="flex items-center gap-2 rounded px-2 py-1.5 text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3"
							:class="
								page === item.key
									? 'bg-surface-base text-ink-gray-9 shadow-sm'
									: 'text-ink-gray-7 hover:bg-surface-gray-2'
							"
							@click="go(item.key)"
						>
							<component :is="item.icon" class="size-4 text-ink-gray-6" />
							{{ item.label }}
						</button>
					</template>
				</nav>

				<!-- Page -->
				<div class="min-h-0 min-w-0 flex-1">
					<RecordsPage
						v-if="page === 'teams'"
						@dirty="dirty = $event"
						kind="teams"
						title="Teams"
						singular="Team"
						name-field="team_name"
						description="A group of sales users and the numbers they answer. Members see every chat on the team's numbers, and new chats are handed out round robin, by load, or by hand."
						empty="No team yet. A team takes the chats of its numbers and gives them to its members."
						@saved="refreshInbox"
					/>
					<RecordsPage
						v-else-if="page === 'numbers'"
						ref="numbersPage"
						@dirty="dirty = $event"
						kind="numbers"
						title="WhatsApp numbers"
						singular="Number"
						description="Every number connected in Periskope, with its team, its rep and its connection."
						empty="No number yet. Add a number, or sync the numbers from Periskope."
						@saved="refreshInbox"
					>
						<template v-if="my.admin" #actions="{ reload }">
							<Button
								label="Sync from Periskope"
								:loading="syncing"
								@click="syncNumbers(reload)"
							/>
							<Button variant="solid" label="Add a number" @click="addNumber" />
						</template>
						<template #list="{ rows, edit }">
							<NumberCards
								:rows="rows"
								:admin="my.admin"
								@edit="edit"
								@link="openLink"
							/>
						</template>
					</RecordsPage>
					<RecordsPage
						v-else-if="page === 'agents'"
						@dirty="dirty = $event"
						kind="agents"
						title="Agents"
						singular="Agent"
						name-field="user"
						description="Give a user one or more numbers: they see every chat on those numbers and no other number, whatever their role. Team members get their team's numbers too. A Sales Manager with no numbers sees every number."
						empty="No agent yet."
						@saved="refreshInbox"
					>
						<template #actions="{ reload }">
							<Button label="Invite a new user" @click="openInvite(reload)" />
						</template>
					</RecordsPage>
					<RecordsPage
						v-else-if="page === 'sla'"
						@dirty="dirty = $event"
						kind="sla"
						title="SLA policies"
						singular="Policy"
						name-field="policy_name"
						description="How soon a customer must get an answer, and a chat be resolved. A policy covers its numbers and teams; the default covers the rest."
						empty="No policy yet: chats have no SLA."
					/>
					<RecordsPage
						v-else-if="page === 'quick_replies'"
						@dirty="dirty = $event"
						kind="quick_replies"
						title="Quick replies"
						singular="Quick reply"
						description="Everyone's quick replies. Shared ones are offered to everybody in the composer (type /)."
					/>
					<RecordsPage
						v-else-if="page === 'labels'"
						@dirty="dirty = $event"
						kind="labels"
						title="Labels and AI tags"
						singular="Label"
						name-field="tag_name"
						description="The AI tags each customer message with one of these; reps put them on chats as labels. Switch one off instead of deleting it."
						@saved="refreshInbox"
					/>
					<SettingsSection
						v-else-if="sectionKeys.includes(page)"
						@dirty="dirty = $event"
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
				<FormControl v-model="invite.email" label="Email" autofocus />
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
			<Button
				variant="solid"
				label="Invite"
				:loading="inviting"
				:disabled="!invite.email"
				@click="sendInvite"
			/>
		</template>
	</Dialog>
	<PhoneLinkDialog
		v-model="link.open"
		:number="link.number"
		:relink="link.relink"
		:add="link.add"
		@changed="numbersChanged"
	/>
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
import LucidePalette from "~icons/lucide/palette";
import LucideMessageCircle from "~icons/lucide/message-circle";
import LucideX from "~icons/lucide/x";
import RecordsPage from "@/components/settings/RecordsPage.vue";
import SettingsSection from "@/components/settings/SettingsSection.vue";
import Picker from "@/components/settings/Picker.vue";
import PhoneLinkDialog from "@/components/settings/PhoneLinkDialog.vue";
import NumberCards from "@/components/settings/NumberCards.vue";
import { boot } from "@/boot";
import { loadCounts, loadTeams } from "@/stores/inbox";
import { ask, discardChanges } from "@/utils/confirm";
import { errorText, showError } from "@/utils/errors";
import { loadFailed } from "@/utils/load";
import { isMobile } from "@/utils/screen";

const open = defineModel({ type: Boolean, default: false });
const page = ref("teams");
const dirty = ref(false); // the open record or section has unsaved changes

async function go(key) {
	if (key === page.value) return;
	if (dirty.value && !(await discardChanges())) return;
	dirty.value = false;
	page.value = key;
}
// The phone's select: when the change is refused (unsaved changes) it shows the open page again.
async function pick(e) {
	await go(e.target.value);
	e.target.value = page.value;
}
async function close() {
	if (dirty.value && !(await discardChanges())) return;
	open.value = false;
}
const my = ref({ manager: boot.is_manager, admin: boot.is_admin });
const settings = ref(null);
const syncing = ref(false);
// Connect / Re-link a Periskope number by its QR code (PhoneLinkDialog)
const numbersPage = ref(null);
const link = ref({ open: false, number: null, relink: false, add: false });
async function openLink(row) {
	const relink = !!row.is_ready;
	if (
		relink &&
		!(await ask(
			"This logs WhatsApp out of Periskope for this number. Chats stop coming in until a phone scans the new code.",
			{
				title: "Re-link this number?",
				confirmLabel: "Log out and show the code",
				danger: true,
			}
		))
	)
		return;
	link.value = { open: true, number: row, relink, add: false };
}
async function addNumber() {
	const ok = await ask(
		"This adds a new phone to your Periskope account, then shows a QR code to scan with the WhatsApp to add. Periskope may bill for each phone on your plan.",
		{ title: "Add a WhatsApp number?", confirmLabel: "Add and show the code" }
	);
	if (ok) link.value = { open: true, number: null, relink: false, add: true };
}
function numbersChanged() {
	numbersPage.value?.reload();
	refreshInbox();
}
const inviteOpen = ref(false);
const invite = ref({ email: "", first_name: "", numbers: [], send_welcome_email: false });
const inviteError = ref("");
let reloadAgents = null;

const sectionKeys = [
	"connect",
	"ai",
	"voice",
	"team",
	"auto_reply",
	"test_mode",
	"meta",
	"branding",
];
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
						{ key: "meta", label: "Meta WhatsApp", icon: LucideMessageCircle },
						{ key: "ai", label: "AI", icon: LucideBot },
						{ key: "voice", label: "Voice notes", icon: LucideMic },
						{ key: "team", label: "Approvers and alerts", icon: LucideBellRing },
						{ key: "auto_reply", label: "Auto reply and hours", icon: LucideClock },
						{ key: "test_mode", label: "Test mode", icon: LucideFlaskConical },
						{ key: "branding", label: "Branding", icon: LucidePalette },
					],
				},
		  ]
		: []),
]);

async function loadSettings() {
	if (!my.value.admin) return;
	try {
		settings.value = await call("periskope_connector.api.admin.get_settings");
	} catch (e) {
		loadFailed(e, "Could not load the settings.");
	}
}

async function settingsSaved(data) {
	if (data) settings.value = data;
	else loadSettings();
	// the new name, logo, colour, festival switch, Meta number or test mode shows at once
	if (["branding", "meta", "test_mode"].includes(page.value)) {
		const fresh = await call("periskope_connector.api.inbox.get_boot");
		boot.brand = fresh.brand;
		boot.festival = fresh.festival;
		boot.test_mode = fresh.test_mode;
		boot.numbers = fresh.numbers;
		refreshInbox();
	}
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
		showError(e, "Could not sync.");
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

const inviting = ref(false);
async function sendInvite() {
	if (inviting.value) return;
	inviting.value = true;
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
		inviteError.value = errorText(e, "Not added.");
	} finally {
		inviting.value = false;
	}
}

// It is mounted when first opened (App.vue), so the first run is at once.
watch(
	open,
	async (o) => {
		if (!o) return;
		try {
			my.value = await call("periskope_connector.api.admin.my_access");
		} catch (e) {
			return loadFailed(e, "Could not open the settings.");
		}
		loadSettings();
	},
	{ immediate: true }
);
watch(page, (p) => sectionKeys.includes(p) && !settings.value && loadSettings());
</script>
