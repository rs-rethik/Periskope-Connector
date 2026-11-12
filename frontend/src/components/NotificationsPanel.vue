<template>
	<div class="flex flex-col">
		<div class="flex shrink-0 items-center gap-1 border-b border-outline-gray-1 px-3 py-2">
			<span class="text-base font-medium text-ink-gray-9">Notifications</span>
			<Button
				v-if="inbox.bell.unread"
				class="ml-auto"
				size="sm"
				variant="ghost"
				label="Mark all read"
				@click="readAll"
			/>
			<Button
				v-if="sheet"
				class="tap"
				:class="inbox.bell.unread ? '' : 'ml-auto'"
				variant="ghost"
				aria-label="Close"
				@click="emit('close')"
			>
				<template #icon><LucideX class="size-4" /></template>
			</Button>
		</div>
		<div class="overflow-y-auto" :class="sheet ? 'min-h-0 flex-1' : 'max-h-[28rem]'">
			<button
				v-for="n in inbox.bell.notifications"
				:key="n.name"
				class="flex w-full gap-2 border-b border-outline-gray-1 px-3 py-2 text-left last:border-0 hover:bg-surface-gray-1"
				@click="open(n)"
			>
				<span
					class="mt-1.5 size-2 shrink-0 rounded-full"
					:class="n.read ? 'bg-transparent' : 'bg-surface-blue-3'"
				/>
				<span class="min-w-0 flex-1">
					<span class="block text-sm text-ink-gray-8">{{ n.subject }}</span>
					<span class="text-xs text-ink-gray-5"
						>{{ kinds[n.type] || n.type }} · {{ fromNow(n.creation) }}</span
					>
				</span>
			</button>
			<div
				v-if="!inbox.bell.notifications.length"
				class="p-4 text-center text-sm text-ink-gray-5"
			>
				Nothing yet. You'll see assignments, mentions and overdue replies here.
			</div>
		</div>
		<!-- notifications on this phone or computer, also when the app is closed -->
		<div
			v-if="push.available"
			class="flex shrink-0 flex-col gap-2 border-t border-outline-gray-1 px-3 py-2.5"
		>
			<div class="flex items-center gap-2">
				<LucideBellRing class="size-4 shrink-0 text-ink-gray-5" />
				<span class="min-w-0 flex-1 text-sm text-ink-gray-8">
					{{ push.on ? "On this device" : "Get them on this device" }}
				</span>
				<Button
					size="sm"
					:variant="push.on ? 'subtle' : 'solid'"
					:label="push.on ? 'Turn off' : 'Turn on'"
					:loading="push.busy"
					@click="togglePush"
				/>
			</div>
			<div v-if="push.on" class="flex items-center gap-1.5 text-xs text-ink-gray-6">
				Quiet from
				<input
					v-model="push.quiet_from"
					type="time"
					class="rounded border-outline-gray-2 bg-surface-gray-1 px-1 py-0.5 text-xs"
					@change="savePrefs"
				/>
				to
				<input
					v-model="push.quiet_to"
					type="time"
					class="rounded border-outline-gray-2 bg-surface-gray-1 px-1 py-0.5 text-xs"
					@change="savePrefs"
				/>
			</div>
			<p v-if="push.error" class="text-xs text-ink-red-7">{{ push.error }}</p>
		</div>
	</div>
</template>

<script setup>
import { onMounted, reactive } from "vue";
import { useRouter } from "vue-router";
import { Button, call } from "frappe-ui";
import LucideX from "~icons/lucide/x";
import LucideBellRing from "~icons/lucide/bell-ring";
import { pushOn, pushSupported, turnPushOff, turnPushOn } from "@/utils/push";
import { inbox, loadBell } from "@/stores/inbox";
import { fromNow } from "@/utils/format";
import { loadFailed } from "@/utils/load";

// The list of notifications: in the sidebar's popover (NotificationsBell), and as a full-screen
// sheet on a phone (App.vue), where it has its own Close button.
defineProps({ sheet: { type: Boolean, default: false } });
const emit = defineEmits(["close"]);
const router = useRouter();
const kinds = { Assignment: "Assigned to you", Mention: "Mentioned you", Alert: "SLA alert" };

// The chat opens at once; marking the notification read happens beside it and never holds it up.
function open(n) {
	if (!n.read)
		call("periskope_connector.api.notifications.mark_read", { name: n.name })
			.then(loadBell)
			.catch((e) => loadFailed(e, "Could not mark the notification read."));
	emit("close");
	router.push({ name: "Inbox", params: { chat: n.document_name } });
}

async function readAll() {
	try {
		await call("periskope_connector.api.notifications.mark_read");
		loadBell();
	} catch (e) {
		loadFailed(e, "Could not mark the notifications read.");
	}
}

// Device notifications (web push): offered when the site and this browser support them.
const push = reactive({
	available: false,
	on: false,
	busy: false,
	error: "",
	key: "",
	quiet_from: "",
	quiet_to: "",
});
onMounted(async () => {
	if (!pushSupported()) return;
	try {
		const cfg = await call("periskope_connector.api.notifications.push_config");
		if (!cfg?.available) return;
		Object.assign(push, {
			available: true,
			key: cfg.public_key,
			quiet_from: cfg.quiet_from,
			quiet_to: cfg.quiet_to,
			on: await pushOn(),
		});
	} catch (e) {
		push.available = false;
	}
});

async function togglePush() {
	push.busy = true;
	push.error = "";
	try {
		if (push.on) await turnPushOff();
		else await turnPushOn(push.key);
		push.on = !push.on;
	} catch (e) {
		push.error = e?.message || "Could not change the notifications on this device.";
	} finally {
		push.busy = false;
	}
}

function savePrefs() {
	call("periskope_connector.api.notifications.save_preferences", {
		push_off: 0,
		quiet_from: push.quiet_from,
		quiet_to: push.quiet_to,
	}).catch((e) => loadFailed(e, "Could not save the quiet hours."));
}
</script>
