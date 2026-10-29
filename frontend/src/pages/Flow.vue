<template>
	<div class="flow flex h-full min-h-0 flex-col">
		<div class="page-head gap-x-4">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">How a message moves</h1>
			<span
				v-for="s in legend"
				:key="s.label"
				class="inline-flex items-center gap-1.5 text-xs text-ink-gray-6"
			>
				<component :is="s.icon" class="size-3.5" :class="s.cls" />{{ s.label }}
			</span>
			<span class="text-xs text-ink-gray-5"
				>Click a step. Drag to move, Ctrl + scroll to zoom.</span
			>
			<Button class="ml-auto" label="Refresh figures" :loading="loading" @click="load" />
			<Button label="Health" @click="$router.push({ name: 'Health' })" />
		</div>

		<div class="panel-frame page-card flex min-h-0 flex-1 overflow-hidden">
			<!-- The diagram -->
			<div
				ref="canvas"
				class="relative min-w-0 flex-1 cursor-grab overflow-hidden bg-surface-gray-1"
				:class="panning ? 'cursor-grabbing' : ''"
				@pointerdown="startPan"
				@wheel.prevent="wheel"
			>
				<div
					class="absolute left-0 top-0 origin-top-left"
					:style="{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }"
				>
					<svg
						v-if="data"
						viewBox="0 0 1000 805"
						width="1000"
						height="805"
						role="group"
						aria-label="How a WhatsApp message moves through the app"
					>
						<defs>
							<marker
								id="fl-arrow"
								viewBox="0 0 10 10"
								refX="9"
								refY="5"
								markerWidth="7"
								markerHeight="7"
								orient="auto-start-reverse"
							>
								<path d="M0 0L10 5L0 10z" class="fill-[var(--edge)]" />
							</marker>
						</defs>
						<rect
							v-for="y in [20, 215, 410, 605]"
							:key="y"
							x="0"
							:y="y"
							width="1000"
							height="150"
							class="fill-[var(--band)]"
						/>
						<text
							v-for="l in LANES"
							:key="l[1]"
							x="14"
							:y="l[0]"
							class="fill-[var(--muted)] text-[11px] font-semibold uppercase tracking-wider"
						>
							{{ l[1] }}
						</text>
						<g v-for="(e, i) in EDGES" :key="i">
							<path
								:d="e[0]"
								fill="none"
								class="stroke-[var(--edge)]"
								stroke-width="1.5"
								:stroke-dasharray="e[1] ? '5 4' : null"
								marker-end="url(#fl-arrow)"
							/>
							<text
								v-if="e[2]"
								:x="e[3]"
								:y="e[4]"
								:text-anchor="e[5] || null"
								class="fill-[var(--muted)] text-[10.5px]"
							>
								{{ e[2] }}
							</text>
						</g>
						<g
							v-for="n in NODES"
							:key="n.id"
							:transform="`translate(${COL[n.col]},${ROW[n.row]})`"
							class="cursor-pointer outline-none"
							tabindex="0"
							role="button"
							@click="pick(n.id)"
							@keydown.enter="pick(n.id)"
						>
							<rect
								width="200"
								height="90"
								rx="8"
								class="fill-[var(--card)]"
								:class="
									selected === n.id
										? 'stroke-[var(--accent)]'
										: 'stroke-[var(--line)]'
								"
								:stroke-width="selected === n.id ? 2.4 : 1.2"
							/>
							<rect
								width="5"
								height="90"
								rx="2"
								:style="{ fill: `var(--st-${data[n.id]?.status || 'ok'})` }"
							/>
							<text
								x="18"
								y="26"
								class="fill-[var(--ink)] text-[13.5px] font-semibold"
							>
								{{ n.title }}
							</text>
							<text x="18" y="44" class="fill-[var(--muted)] text-[12px]">
								{{ n.sub }}
							</text>
							<text x="18" y="72" class="fill-[var(--ink)] font-mono text-[12px]">
								{{ data[n.id]?.headline || "" }}
							</text>
						</g>
					</svg>
				</div>
				<div class="absolute bottom-3 left-3 flex gap-1.5">
					<Button title="Zoom in" @click.stop="zoom(1.2)">+</Button>
					<Button title="Zoom out" @click.stop="zoom(1 / 1.2)">−</Button>
					<Button title="Fit to view" @click.stop="fit">⤢</Button>
				</div>
			</div>

			<!-- The panel of the step -->
			<aside
				class="w-[380px] shrink-0 overflow-y-auto border-l border-outline-gray-1 bg-surface-base p-4"
			>
				<template v-if="panel">
					<div class="text-xs uppercase tracking-wide text-ink-gray-5">
						{{ panel.kick }}
					</div>
					<h2 class="mt-0.5 text-lg font-semibold text-ink-gray-9">{{ panel.title }}</h2>
					<p class="mt-1 text-sm text-ink-gray-6">{{ panel.text }}</p>
					<dl class="mt-3 grid grid-cols-[1fr,auto] gap-x-4 gap-y-1.5 text-sm">
						<template v-for="r in panel.rows" :key="r[0]">
							<dt class="text-ink-gray-5">{{ r[0] }}</dt>
							<dd class="text-right text-ink-gray-8">{{ r[1] ?? "—" }}</dd>
						</template>
					</dl>
					<div v-if="panel.warn" class="mt-2 text-xs text-ink-amber-8">
						{{ panel.warn }}
					</div>

					<!-- What can be changed here, saved at once (admins) -->
					<div
						v-if="panel.controls?.length && boot.is_admin"
						class="mt-4 rounded-lg border border-outline-gray-1 bg-surface-gray-1 p-3"
					>
						<div class="mb-2 text-xs font-medium uppercase text-ink-gray-5">
							Settings
						</div>
						<div v-for="c in panel.controls" :key="c.field" class="mb-2">
							<FormControl
								v-if="c.type === 'check'"
								type="checkbox"
								:label="c.label"
								:model-value="!!s[c.field]"
								@update:model-value="(v) => save(c.field, v ? 1 : 0)"
							/>
							<template v-else>
								<label class="mb-1 block text-xs text-ink-gray-5">{{
									c.label
								}}</label>
								<FormControl
									:type="c.type"
									:options="c.options"
									:model-value="
										c.type === 'time'
											? String(s[c.field] || '').slice(0, 5)
											: s[c.field]
									"
									@change="(e) => save(c.field, e.target.value)"
								/>
							</template>
						</div>
					</div>

					<!-- Tags: on and off -->
					<div v-if="selected === 'tags'" class="mt-4">
						<table class="w-full text-sm">
							<thead class="text-left text-xs text-ink-gray-5">
								<tr>
									<th class="py-1">Tag</th>
									<th class="py-1 text-right">Messages</th>
									<th class="py-1 text-right">On</th>
								</tr>
							</thead>
							<tbody>
								<tr
									v-for="t in data.tags.tags"
									:key="t.name"
									class="border-t border-outline-gray-1"
								>
									<td class="py-1.5 text-ink-gray-8">
										{{ t.name
										}}<span v-if="t.default" class="text-xs text-ink-gray-5">
											(default)</span
										>
									</td>
									<td class="py-1.5 text-right text-ink-gray-8">
										{{ t.messages }}
									</td>
									<td class="py-1.5 text-right">
										<Switch
											v-if="boot.is_admin"
											:model-value="!!t.active"
											@update:model-value="(v) => tagActive(t, v)"
										/>
										<span v-else>{{ t.active ? "Yes" : "No" }}</span>
									</td>
								</tr>
							</tbody>
						</table>
					</div>

					<!-- Numbers -->
					<div v-if="selected === 'periskope'" class="mt-4">
						<table class="w-full text-sm">
							<thead class="text-left text-xs text-ink-gray-5">
								<tr>
									<th class="py-1">Number</th>
									<th class="py-1">Rep</th>
									<th class="py-1">State</th>
								</tr>
							</thead>
							<tbody>
								<tr
									v-for="p in data.periskope.phones"
									:key="p.number"
									class="border-t border-outline-gray-1"
								>
									<td class="py-1.5 text-ink-gray-8">{{ p.name }}</td>
									<td class="py-1.5 text-ink-gray-7">{{ p.rep || "" }}</td>
									<td class="py-1.5 text-ink-gray-7">{{ p.state }}</td>
								</tr>
							</tbody>
						</table>
					</div>

					<!-- Reminder ladder -->
					<div v-if="selected === 'sla'" class="mt-4">
						<table class="w-full text-sm">
							<thead class="text-left text-xs text-ink-gray-5">
								<tr>
									<th class="py-1">Wait</th>
									<th class="py-1">From</th>
									<th class="py-1">Action</th>
								</tr>
							</thead>
							<tbody>
								<tr
									v-for="(l, i) in data.sla.levels"
									:key="i"
									class="border-t border-outline-gray-1"
								>
									<td class="py-1.5 text-ink-gray-8">{{ minutes(l.wait) }}</td>
									<td class="py-1.5 text-ink-gray-7">{{ l.severity }}</td>
									<td class="py-1.5 text-ink-gray-7">{{ l.action }}</td>
								</tr>
							</tbody>
						</table>
					</div>

					<div v-if="panel.links?.length" class="mt-4 flex flex-wrap gap-2">
						<Button
							v-for="l in panel.links"
							:key="l.label"
							:variant="l.primary ? 'solid' : 'subtle'"
							:label="l.label"
							@click="$router.push(l.to)"
						/>
					</div>
				</template>
				<div v-else class="text-sm text-ink-gray-5">
					Click a step of the diagram to see its figures.
				</div>
			</aside>
		</div>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { Button, FormControl, Switch, call, toast } from "frappe-ui";
import LucideCircleCheck from "~icons/lucide/circle-check";
import LucideTriangleAlert from "~icons/lucide/triangle-alert";
import LucideCircleX from "~icons/lucide/circle-x";
import LucideCircleMinus from "~icons/lucide/circle-minus";
import { boot } from "@/boot";

// The Flow diagram (flow.py): four lanes,
// the steps at fixed places, and the arrows between them.
const COL = { A: 24, B: 272, C: 520, D: 768 };
const ROW = { 1: 60, 2: 255, 3: 450, 4: 645 };
const LANES = [
	[38, "1 · Capture"],
	[233, "2 · Understand"],
	[428, "3 · Follow up"],
	[623, "4 · AI agent"],
];
const NODES = [
	{ id: "periskope", col: "A", row: 1, title: "Periskope", sub: "Your WhatsApp numbers" },
	{ id: "capture", col: "B", row: 1, title: "Capture", sub: "Webhook + hourly catch-up" },
	{ id: "link", col: "C", row: 1, title: "Link to CRM", sub: "Lead or deal by number" },
	{ id: "queue", col: "D", row: 1, title: "Contact queue", sub: "Unknown numbers" },
	{ id: "files", col: "A", row: 2, title: "Attachments", sub: "Saved on the lead or deal" },
	{ id: "ai", col: "B", row: 2, title: "AI reads", sub: "Text, images, PDFs, voice" },
	{ id: "tags", col: "C", row: 2, title: "Tags", sub: "Categories for reports" },
	{ id: "decisions", col: "D", row: 2, title: "AI decides", sub: "Tasks, escalation, leads" },
	{ id: "waiting", col: "B", row: 3, title: "Customer waiting?", sub: "Working hours only" },
	{ id: "sla", col: "C", row: 3, title: "Reminder ladder", sub: "bell, task, managers, MD" },
	{ id: "people", col: "D", row: 3, title: "People told", sub: "Email and bell" },
	{ id: "autoreply", col: "A", row: 4, title: "Auto reply", sub: "When no rep answers in time" },
	{ id: "crm", col: "B", row: 4, title: "CRM updates", sub: "Summary, notes, tasks" },
	{
		id: "agent",
		col: "C",
		row: 4,
		title: "Learning agent",
		sub: "Learns; asks before config changes",
	},
];
const EDGES = [
	["M224 105H272"],
	["M472 105H520"],
	["M720 105H768", "dash", "no match", 744, 97, "middle"],
	["M372 150V253", "", "every message", 380, 205],
	["M372 150V190H124V253", "dash", "files", 132, 184],
	["M224 300H272"],
	["M472 300H520"],
	["M720 300H768"],
	["M372 345V448", "", "needs a reply", 380, 400],
	["M472 495H520"],
	["M720 495H768"],
	["M868 345V448", "", "escalate", 876, 400],
	["M868 255V190H620V152", "dash", "lead, timeline comment", 636, 184],
	["M372 540V598H124V643", "", "auto reply", 250, 592],
	["M224 690H272"],
	["M472 690H520"],
];
const legend = [
	{ label: "Working", icon: LucideCircleCheck, cls: "text-ink-green-6" },
	{ label: "Needs a look", icon: LucideTriangleAlert, cls: "text-ink-amber-8" },
	{ label: "Not working", icon: LucideCircleX, cls: "text-ink-red-8" },
	{ label: "Off", icon: LucideCircleMinus, cls: "text-ink-gray-5" },
];

const data = ref(null);
const loading = ref(false);
const selected = ref("periskope");
const canvas = ref(null);
const view = reactive({ x: 0, y: 0, k: 1 });
const panning = ref(false);
const s = computed(() => data.value?.settings || {});

function minutes(m) {
	return m < 60 ? `${m} min` : `${+(m / 60).toFixed(1)} h`;
}

const list = (kind, label, primary) => ({
	label,
	to: { name: "DataList", params: { kind } },
	primary,
});
const report = (name, label) => ({ label, to: { name: "Reports", params: { report: name } } });

const panel = computed(() => {
	const d = data.value;
	if (!d) return null;
	const P = {
		periskope: () => ({
			kick: "Step 1",
			title: "Periskope",
			text: "Your WhatsApp company numbers, linked in Periskope. The app reads every message; it sends only the AI replies and the reps' replies (in test mode only to the allowed numbers).",
			rows: [["Connection", d.periskope.connection]],
		}),
		capture: () => ({
			kick: "Step 2",
			title: "Capture",
			text: "Periskope calls the webhook for every message. If a call is lost, the hourly catch-up reads the chat history and fills the gap.",
			rows: [
				["Last webhook", d.capture.last_webhook],
				["Messages today", d.capture.today],
				["Recovered by catch-up (24 h)", d.capture.recovered],
				["Unprocessed logs", d.capture.unprocessed],
				["Events asked from Periskope", (d.capture.events || []).join(", ") || "None"],
			],
			controls: [{ field: "enabled", label: "Enable Periskope Integration", type: "check" }],
			links: [list("webhook_logs", "Webhook logs")],
		}),
		link: () => ({
			kick: "Step 3",
			title: "Link to CRM",
			text: "Each message is matched to a lead or deal by the customer's mobile number. Groups are matched by name, sender or the customer the AI finds in the text.",
			rows: [
				["Linked", d.link.counts.Linked],
				["Internal team groups", d.link.counts.Internal],
				["Ambiguous", d.link.counts.Ambiguous],
				["No match", d.link.counts["No Match"]],
			],
			links: [
				report("Periskope Unmatched Numbers", "Unmatched numbers"),
				list("groups", "Groups"),
			],
		}),
		queue: () => ({
			kick: "Step 3b",
			title: "Contact queue",
			text: "A number that is not on any lead, deal or contact waits here. The AI makes a lead itself for a new business enquiry; the rest can be linked or ignored.",
			rows: [
				["New", d.queue.new],
				["Lead created", d.queue.lead_created],
				["Ignored", d.queue.ignored],
			],
			links: [list("contacts", "Open the queue", true)],
		}),
		files: () => ({
			kick: "Step 2b",
			title: "Attachments",
			text: "Every image, document, video and voice note is saved and attached to the lead or deal. Images and PDFs are read by the AI.",
			rows: [
				["Saved today", d.files.saved],
				["Read by the AI today", d.files.read],
				["Still to save (24 h)", d.files.waiting],
			],
		}),
		ai: () => ({
			kick: "Step 4",
			title: "AI reads",
			text: "Every customer message goes to the AI as it arrives, with images, PDFs and voice notes. It returns a tag, a severity, the mood, a summary and what to do next.",
			rows: [
				["Model", d.ai.model],
				["Waiting to be tagged", d.ai.pending],
				["Failed", d.ai.failed],
				["Tagged", d.ai.tagged],
				["Corrected by managers", d.ai.corrected],
				["Tokens this month", d.ai.tokens],
			],
			links: [
				report("Periskope Tagging Review", "Tagging review"),
				list("ai_usage", "AI usage"),
			],
		}),
		tags: () => ({
			kick: "Step 5",
			title: "Tags",
			text: "The names the AI can give a message. The description decides what the AI puts under each. Switches save at once.",
			rows: [
				["Active tags", d.tags.tags.filter((t) => t.active).length],
				["Default tag", d.tags.default],
			],
		}),
		decisions: () => ({
			kick: "Step 6",
			title: "AI decides",
			text: "No rules: the AI decides for each message. Work handed out in a group becomes a CRM Task; a problem goes to the managers or the MD; a new enquiry becomes a lead; what matters shows on the record's timeline.",
			rows: [
				["Tasks created today", d.decisions.tasks],
				["Escalated to managers today", d.decisions.managers],
				["Escalated to the MD today", d.decisions.md],
				["Leads created today", d.decisions.leads],
				["Shown on timelines today", d.decisions.timeline],
			],
			links: [list("agent_actions", "What the agent did", true)],
		}),
		waiting: () => ({
			kick: "Step 7",
			title: "Customer waiting?",
			text: "A customer message that needs a reply and has no answer starts the clock. Only working hours count.",
			rows: [["Waiting now", d.waiting.waiting]],
			controls: [
				{ field: "working_hours_start", label: "From", type: "time" },
				{ field: "working_hours_end", label: "To", type: "time" },
				{
					field: "working_days",
					label: "Days",
					type: "select",
					options: ["Monday to Saturday", "Monday to Friday", "Every day"],
				},
			],
		}),
		sla: () => ({
			kick: "Step 8",
			title: "Reminder ladder",
			text: "What happens as a customer keeps waiting, in working time. With auto reply on, the AI has usually answered long before.",
			rows: [
				["Open reminders", d.sla.open],
				["At level 2 or 3", d.sla.deep],
			],
			links: [list("reminders", "Reminders")],
		}),
		people: () => ({
			kick: "Step 9",
			title: "People told",
			text: "Everyone the app contacts: the rep gets a bell and a task, managers and the MD get the AI's escalations by email.",
			rows: [
				["Emails today", d.people.emails_today],
				["Outgoing email", d.people.mail_ready ? "Configured" : "Not set up"],
			],
			warn: d.people.mail_ready
				? ""
				: "Without an outgoing Email Account no alert email is sent.",
			controls: [
				{ field: "alert_emails", label: "Managers", type: "textarea" },
				{ field: "escalation_emails", label: "MD (with the managers)", type: "textarea" },
			],
		}),
		autoreply: () => {
			const t = d.autoreply.today || {};
			return {
				kick: "Step 4",
				title: "Auto reply",
				text: "When no rep has answered a customer within the set time, the AI answers from the knowledge base, ERPNext and the deal's last quotation. When the data does not answer it, the customer gets a holding reply and the rep a task. A new number is welcomed at once and asked for their name, company, city and email, which fill its lead. Never in groups.",
				rows: [
					["Answered today", t.Sent || 0],
					["Holding replies today", t["Holding Sent"] || 0],
					["New numbers welcomed today", t["Welcome Sent"] || 0],
					["Dry runs today (test mode)", (t["Dry Run"] || 0) + (t.Blocked || 0)],
					["Failed today", t.Failed || 0],
					["Waiting now", d.autoreply.waiting],
				],
				controls: [
					{ field: "enable_auto_reply", label: "Reply to customers", type: "check" },
					{
						field: "reply_delay_minutes",
						label: "When no rep answers within (minutes)",
						type: "number",
					},
					{ field: "welcome_new_numbers", label: "Welcome new numbers", type: "check" },
				],
				links: [list("auto_replies", "All auto replies", true)],
			};
		},
		crm: () => ({
			kick: "Step 4b",
			title: "CRM updates",
			text: "On every lead or deal the agent keeps one WhatsApp summary note, notes, follow-up tasks, a timeline entry for each AI reply, and the status when the conversation clearly moves on.",
			rows: [
				["Notes written today", d.crm.notes],
				["Tasks written today", d.crm.tasks],
				["Status changes today", d.crm.status_changes],
				["Timeline entries today", d.crm.timeline],
				["Dry runs today (test mode)", d.crm.dry_runs],
			],
			links: [list("agent_actions", "What the agent did", true)],
		}),
		agent: () => ({
			kick: "Step 4c",
			title: "Learning agent",
			text: "Every 15 minutes the agent reads the finished conversations and updates the knowledge base. It suggests changes to tags, group mappings, ignored numbers and a few settings; those wait for an approver.",
			rows: [
				[
					"Last run",
					`${d.agent.last_run}${d.agent.last_status ? ` (${d.agent.last_status})` : ""}`,
				],
				["Waiting for approval", d.agent.pending],
				["Knowledge entries in use", d.agent.knowledge],
				["Test mode", d.agent.test_mode ? "On" : "Off"],
			],
			warn: d.agent.last_error || "",
			links: [
				list("suggestions", "Suggestions", true),
				{ label: "Knowledge base", to: { name: "Knowledge" } },
				list("agent_runs", "Agent runs"),
			],
		}),
	};
	return P[selected.value]?.() || null;
});

function pick(id) {
	if (!dragged) selected.value = id;
}

async function load() {
	loading.value = true;
	try {
		data.value = await call("periskope_connector.api.settings.get_flow");
	} finally {
		loading.value = false;
	}
}

async function save(field, value) {
	try {
		await call("periskope_connector.api.settings.set_flow_setting", { field, value });
		toast.success("Saved");
		load();
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "Not saved.");
		load();
	}
}

async function tagActive(t, on) {
	try {
		await call("periskope_connector.api.settings.set_tag_active", {
			tag: t.name,
			active: on ? 1 : 0,
		});
		t.active = on;
	} catch (e) {
		toast.error(e?.messages?.[0] || e?.message || "Not saved.");
	}
}

// --- Pan and zoom ---------------------------------------------------------------------------------

let dragged = false;
function startPan(ev) {
	if (ev.button !== 0 || ev.target.closest("button")) return;
	const start = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y };
	dragged = false;
	const move = (m) => {
		const dx = m.clientX - start.x;
		const dy = m.clientY - start.y;
		if (!dragged && Math.abs(dx) + Math.abs(dy) < 4) return; // still a click
		dragged = true;
		panning.value = true;
		view.x = start.vx + dx;
		view.y = start.vy + dy;
	};
	const up = () => {
		panning.value = false;
		window.removeEventListener("pointermove", move);
		window.removeEventListener("pointerup", up);
		setTimeout(() => (dragged = false), 0); // the click that ends a drag must not pick a step
	};
	window.addEventListener("pointermove", move);
	window.addEventListener("pointerup", up);
}

function zoomAt(factor, cx, cy) {
	const k = Math.min(2.5, Math.max(0.3, view.k * factor));
	view.x = cx - ((cx - view.x) * k) / view.k;
	view.y = cy - ((cy - view.y) * k) / view.k;
	view.k = k;
}

function zoom(factor) {
	const r = canvas.value.getBoundingClientRect();
	zoomAt(factor, r.width / 2, r.height / 2);
}

function wheel(ev) {
	const r = canvas.value.getBoundingClientRect();
	if (ev.ctrlKey || ev.metaKey)
		zoomAt(Math.exp(-ev.deltaY * 0.01), ev.clientX - r.left, ev.clientY - r.top);
	else {
		view.x -= ev.deltaX;
		view.y -= ev.deltaY;
	}
}

function fit() {
	const r = canvas.value?.getBoundingClientRect();
	if (!r) return;
	const k = Math.min(r.width / 1000, r.height / 805) * 0.96;
	view.k = k;
	view.x = (r.width - 1000 * k) / 2;
	view.y = (r.height - 805 * k) / 2;
}

const onResize = () => fit();
onMounted(async () => {
	await load();
	fit();
	window.addEventListener("resize", onResize);
});
onBeforeUnmount(() => window.removeEventListener("resize", onResize));
</script>

<style>
.flow {
	--band: #ffffff8c;
	--card: #ffffff;
	--line: #d4d3cf;
	--edge: #8f8e89;
	--muted: #6f6e69;
	--ink: #1f1f1d;
	--accent: #2a78d6;
	--st-ok: #2f9e5c;
	--st-warn: #d98b00;
	--st-bad: #d6433a;
	--st-off: #a8a7a2;
}
@media (prefers-color-scheme: dark) {
	:root:where(:not([data-theme="light"])) .flow {
		--band: #ffffff0a;
		--card: #232321;
		--line: #3a3a37;
		--edge: #8f8e89;
		--muted: #a8a7a0;
		--ink: #f0efec;
		--accent: #3987e5;
	}
}
:root[data-theme="dark"] .flow {
	--band: #ffffff0a;
	--card: #232321;
	--line: #3a3a37;
	--edge: #8f8e89;
	--muted: #a8a7a0;
	--ink: #f0efec;
	--accent: #3987e5;
}
</style>
