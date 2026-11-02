<template>
	<div class="kb flex h-full min-h-0 flex-col">
		<div class="page-head">
			<h1 class="mr-2 text-lg font-semibold text-ink-gray-9">Knowledge base</h1>
			<span class="text-sm text-ink-gray-5"
				>What the AI answers from. Topics branch into conditions and end in answers.</span
			>
			<div class="ml-auto flex flex-wrap gap-2">
				<Button variant="solid" label="Add topic" @click="addChild(ROOT, 'Topic')" />
				<Button
					:label="gapCount ? `Knowledge gaps (${gapCount})` : 'Knowledge gaps'"
					@click="showGaps"
				/>
				<Button
					label="Import a document"
					:loading="importing"
					@click="$refs.picker.click()"
				/>
				<Button label="Tidy up" @click="tidy(true)" />
				<input
					ref="picker"
					type="file"
					class="hidden"
					accept=".pdf,.xlsx,.csv,.txt,.docx"
					@change="onFile"
				/>
			</div>
		</div>

		<div v-if="!boot.is_admin" class="p-10 text-center text-sm text-ink-gray-6">
			The knowledge base is for admins.
		</div>
		<div
			v-else
			ref="root"
			class="panel-frame page-card relative min-h-0 flex-1 select-none overflow-hidden bg-surface-gray-1"
			:class="panning ? 'cursor-grabbing' : 'cursor-grab'"
			@pointerdown="onPointerDown"
			@wheel.prevent="onWheel"
		>
			<div
				class="absolute left-0 top-0 origin-top-left"
				:style="{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }"
			>
				<!-- wires: the layer is shifted so that (0, 0) sits in its middle; browsers find the
				     pointer on SVG only inside its own box -->
				<svg
					class="pointer-events-none absolute"
					style="left: -10000px; top: -10000px"
					width="20000"
					height="20000"
				>
					<g transform="translate(10000 10000)">
						<template v-for="n in list" :key="n.name">
							<path
								:d="curve(outPoint(parentOf(n)), inPoint(n.name))"
								class="pointer-events-auto cursor-pointer"
								stroke="transparent"
								stroke-width="14"
								fill="none"
								@mouseenter="hoverWire(n.name)"
								@mouseleave="leaveWire"
							/>
							<path
								:d="curve(outPoint(parentOf(n)), inPoint(n.name))"
								fill="none"
								class="stroke-[var(--wire)]"
								stroke-width="2"
							/>
						</template>
						<path
							v-if="temp"
							:d="curve(temp.from, temp.to)"
							fill="none"
							class="stroke-[var(--accent)]"
							stroke-width="2"
							stroke-dasharray="6 4"
						/>
					</g>
				</svg>

				<!-- Customer asks -->
				<div
					class="absolute flex items-center justify-center rounded-2xl border border-[var(--line)] bg-[var(--card)] shadow-sm"
					data-name=""
					:style="{
						left: '0px',
						top: `${-TRIGGER / 2}px`,
						width: `${TRIGGER}px`,
						height: `${TRIGGER}px`,
					}"
				>
					<span
						class="flex size-10 items-center justify-center rounded-lg text-white"
						:style="{ background: TYPES[''].color }"
						v-html="TYPES[''].icon"
					/>
					<span class="absolute -bottom-6 whitespace-nowrap text-xs text-ink-gray-6"
						>Customer asks</span
					>
					<span
						class="out-port absolute -right-2 top-1/2 size-4 -translate-y-1/2 cursor-crosshair rounded-full border-2 border-[var(--card)] bg-[var(--port)]"
						data-out=""
					/>
					<Tooltip text="Add a node after this"
						><button
							class="plus absolute -right-9 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded border border-[var(--line)] bg-[var(--card)] text-ink-gray-6 hover:text-ink-gray-9"
							aria-label="Add a node after this"
							@pointerdown.stop
							@click="openMenu(ROOT, ...outPoint(ROOT))"
						>
							+
						</button></Tooltip
					>
					<span
						v-if="!list.length"
						class="absolute left-24 whitespace-nowrap text-sm text-ink-gray-5"
						>Click + to add your first topic</span
					>
				</div>

				<!-- The nodes -->
				<div
					v-for="n in list"
					:key="n.name"
					:data-name="n.name"
					class="node absolute flex items-center gap-2.5 rounded-xl border bg-[var(--card)] px-2.5 shadow-sm"
					:class="[
						selected === n.name
							? 'border-[var(--accent)] ring-2 ring-[var(--accent-soft)]'
							: 'border-[var(--line)]',
						n.draft ? 'border-dashed' : '',
						n.status === 'Suggested' ? 'opacity-80' : '',
					]"
					:style="{
						left: `${n.x}px`,
						top: `${n.y}px`,
						width: `${NODE_W}px`,
						height: `${NODE_H}px`,
					}"
				>
					<span
						class="in-port absolute -left-1.5 top-1/2 size-3 -translate-y-1/2 rounded-full bg-[var(--port)]"
					/>
					<span
						class="flex size-9 shrink-0 items-center justify-center rounded-lg text-white"
						:style="{ background: TYPES[n.node_type].color }"
						v-html="TYPES[n.node_type].icon"
					/>
					<span class="min-w-0 flex-1">
						<span class="block truncate text-sm font-medium text-ink-gray-9">{{
							n.question || "Untitled"
						}}</span>
						<span class="block truncate text-xs text-ink-gray-5">{{ subOf(n) }}</span>
					</span>
					<span
						v-if="n.draft || n.status === 'Suggested'"
						class="absolute -top-2.5 right-2 rounded bg-surface-amber-2 px-1.5 text-[10px] text-ink-amber-8"
					>
						{{ n.draft ? "Not saved" : "Waiting for approval" }}
					</span>
					<template v-if="CHILDREN[n.node_type].length">
						<span
							class="out-port absolute -right-2 top-1/2 size-4 -translate-y-1/2 cursor-crosshair rounded-full border-2 border-[var(--card)] bg-[var(--port)]"
							:data-out="n.name"
						/>
						<Tooltip text="Add a node after this"
							><button
								class="plus absolute -right-9 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded border border-[var(--line)] bg-[var(--card)] text-ink-gray-6 hover:text-ink-gray-9"
								aria-label="Add a node after this"
								@pointerdown.stop
								@click="openMenu(n.name, ...outPoint(n.name))"
							>
								+
							</button></Tooltip
						>
					</template>
				</div>

				<!-- unhook a wire -->
				<Tooltip v-if="wire" text="Disconnect"
					><button
						class="absolute flex size-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-[var(--line)] bg-[var(--card)] text-ink-gray-6 shadow hover:text-ink-red-8"
						:style="{ left: `${wire.x}px`, top: `${wire.y}px` }"
						aria-label="Disconnect"
						@pointerdown.stop
						@mouseenter="keepWire"
						@mouseleave="wire = null"
						@click="disconnect(wire.child)"
					>
						<LucideX class="size-3.5" /></button
				></Tooltip>
			</div>

			<!-- Add a node -->
			<div
				v-if="menu"
				class="absolute z-20 w-72 rounded-lg border border-outline-gray-2 bg-surface-base p-1 shadow-xl"
				:style="{ left: `${menu.left}px`, top: `${menu.top}px` }"
				@pointerdown.stop
			>
				<div class="px-2 py-1 text-xs font-medium uppercase text-ink-gray-5">
					Add a node
				</div>
				<button
					v-for="t in CHILDREN[typeOf(menu.from)]"
					:key="t"
					class="flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
					@click="addChild(menu.from, t, menu.at)"
				>
					<span
						class="flex size-8 shrink-0 items-center justify-center rounded-lg text-white"
						:style="{ background: TYPES[t].color }"
						v-html="TYPES[t].icon"
					/>
					<span>
						<span class="block text-sm font-medium text-ink-gray-8">{{
							TYPES[t].label
						}}</span>
						<span class="block text-xs text-ink-gray-5">{{ TYPES[t].hint }}</span>
					</span>
				</button>
			</div>

			<div class="absolute bottom-3 left-3 flex gap-1.5" @pointerdown.stop>
				<Button tooltip="Zoom in" aria-label="Zoom in" @click="zoomAt(1.2)">+</Button>
				<Button tooltip="Zoom out" aria-label="Zoom out" @click="zoomAt(1 / 1.2)"
					>−</Button
				>
				<Button tooltip="Fit to view" aria-label="Fit to view" @click="fit">⤢</Button>
			</div>

			<!-- Side panel: a node, or the gaps -->
			<aside
				v-if="panel"
				class="absolute bottom-3 right-3 top-3 z-10 flex w-[360px] flex-col rounded-xl border border-outline-gray-2 bg-surface-base shadow-xl"
				@pointerdown.stop
				@wheel.stop
			>
				<div class="flex items-center gap-2 border-b border-outline-gray-1 px-4 py-3">
					<span
						v-if="panel.kind === 'node'"
						class="flex size-7 items-center justify-center rounded-md text-white"
						:style="{ background: TYPES[editing.node_type].color }"
						v-html="TYPES[editing.node_type].icon"
					/>
					<span class="text-base font-medium text-ink-gray-9">{{ panelTitle }}</span>
					<button
						class="ml-auto text-ink-gray-5 hover:text-ink-gray-8"
						@click="closePanel()"
					>
						<LucideX class="size-4" />
					</button>
				</div>
				<div class="min-h-0 flex-1 overflow-y-auto px-4 py-3">
					<template v-if="panel.kind === 'node'">
						<div
							v-if="note"
							class="mb-3 rounded-md bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-7"
						>
							{{ note }}
						</div>
						<div class="flex flex-col gap-3">
							<FormControl
								v-model="form.question"
								:type="editing.node_type === 'Answer' ? 'textarea' : 'text'"
								:rows="2"
								:label="labels.question"
								:description="labels.questionHint"
							/>
							<Button
								v-if="editing.node_type === 'Condition'"
								size="sm"
								label="Use Otherwise (none of the other branches)"
								@click="form.question = 'Otherwise'"
							/>
							<FormControl
								v-if="editing.node_type === 'Answer'"
								v-model="form.answer"
								type="textarea"
								:rows="5"
								label="Answer"
								description="Only facts the agent may tell any customer. Numbers are checked against this text."
							/>
							<FormControl
								v-if="editing.node_type !== 'Condition'"
								v-model="form.keywords"
								label="Words customers use"
								:description="
									editing.node_type === 'Topic'
										? 'Separated by commas, for example: shipping, courier, dispatch'
										: 'Separated by commas'
								"
							/>
						</div>
						<ErrorMessage class="mt-2" :message="error" />
					</template>
					<template v-else>
						<p class="mb-3 text-xs text-ink-gray-6">
							What customers asked in the last 14 days that the agent could not
							answer, so it sent a holding reply.
						</p>
						<div v-if="!gaps.length" class="text-sm text-ink-gray-5">
							Nothing yet. Every recent question had an answer.
						</div>
						<div
							v-for="(g, i) in gaps"
							:key="i"
							class="border-b border-outline-gray-1 py-2 last:border-0"
						>
							<div class="text-sm text-ink-gray-8">{{ g.question }}</div>
							<div class="flex items-center text-xs text-ink-gray-5">
								{{ fromNow(g.when) }}
								<Button
									class="ml-auto"
									size="sm"
									label="Add an answer"
									@click="
										addChild(ROOT, 'Answer', null, g.question.slice(0, 140))
									"
								/>
							</div>
						</div>
					</template>
				</div>
				<div
					v-if="panel.kind === 'node'"
					class="flex gap-2 border-t border-outline-gray-1 px-4 py-3"
				>
					<Button variant="solid" label="Save" :loading="saving" @click="save" />
					<Button
						v-if="!editing.draft"
						theme="red"
						variant="subtle"
						label="Delete"
						@click="remove(editing.name)"
					/>
				</div>
			</aside>
		</div>

		<Dialog
			v-model="importOpen"
			:options="{
				title: `Answers found in ${imported?.file_name || 'the document'}`,
				size: '2xl',
			}"
		>
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl
						v-model="importParent"
						type="select"
						label="Put them after"
						:options="parentOptions"
					/>
					<p class="text-xs text-ink-gray-5">
						Untick anything the agent should not tell customers. Nothing is saved until
						you add them.
					</p>
					<div class="max-h-[50vh] overflow-y-auto">
						<label
							v-for="(e, i) in imported?.entries || []"
							:key="i"
							class="flex cursor-pointer gap-3 border-b border-outline-gray-1 py-2 last:border-0"
						>
							<input
								v-model="picks"
								type="checkbox"
								:value="i"
								class="mt-1 rounded border-outline-gray-3"
							/>
							<span>
								<span class="block text-sm font-medium text-ink-gray-8">{{
									e.question
								}}</span>
								<span class="block text-sm text-ink-gray-6">{{ e.answer }}</span>
							</span>
						</label>
					</div>
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					:label="`Add ${picks.length} answer(s)`"
					:disabled="!picks.length"
					@click="confirmImport"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { Button, Dialog, ErrorMessage, FormControl, Tooltip, call, toast } from "frappe-ui";
import LucideX from "~icons/lucide/x";
import { boot } from "@/boot";
import { fromNow } from "@/utils/format";
import { ask } from "@/utils/confirm";

// The knowledge base the auto reply answers from, as a node graph. Every node is a Periskope Knowledge Entry and its
// wire is its parent_entry; everything saves as it is made (knowledge_canvas.py).
const API = "periskope_connector.knowledge_canvas.";
const NODE_W = 240;
const NODE_H = 64;
const TRIGGER = 76;
const ROOT_X = 200;
const COL = 340;
const ROW = 92;
const ROOT = "";
const svg = (d) =>
	`<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const TYPES = {
	"": {
		label: "Customer asks",
		color: "#ff6d5a",
		icon: svg('<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>'),
	},
	Topic: {
		label: "Topic",
		color: "#3b82f6",
		hint: "A subject customers ask about, like Delivery or Pricing",
		icon: svg(
			'<path d="M4 20h16a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-8l-2-3H4a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1z"/>'
		),
	},
	Condition: {
		label: "Condition",
		color: "#10b981",
		hint: "A branch that applies only when something is true, like the city",
		icon: svg(
			'<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>'
		),
	},
	Answer: {
		label: "Answer",
		color: "#8b5cf6",
		hint: "What the agent tells the customer",
		icon: svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
	},
};
// Which nodes may hang under which (the same rule as the doctype's PARENTS).
const CHILDREN = {
	"": ["Topic", "Answer"],
	Topic: ["Topic", "Condition", "Answer"],
	Condition: ["Condition", "Answer"],
	Answer: [],
};

const root = ref(null);
const nodes = reactive({});
const suggestions = ref({});
const gapCount = ref(0);
const view = reactive({ x: 0, y: 0, k: 1 });
const selected = ref(null);
const panel = ref(null);
const editing = ref(null);
const form = reactive({ question: "", answer: "", keywords: "" });
const error = ref("");
const saving = ref(false);
const menu = ref(null);
const temp = ref(null);
const wire = ref(null);
const gaps = ref([]);
const panning = ref(false);
const importing = ref(false);
const importOpen = ref(false);
const imported = ref(null);
const importParent = ref("");
const picks = ref([]);
let drafts = 0;
let wireTimer = null;

const list = computed(() => Object.values(nodes));

const parentOf = (n) => (n.parent && nodes[n.parent] ? n.parent : ROOT);
const typeOf = (name) => (name === ROOT ? ROOT : nodes[name]?.node_type);
const subOf = (n) =>
	n.node_type === "Answer"
		? n.answer
		: n.node_type === "Condition"
		? "Only when this is true"
		: n.keywords || "Topic";
const outPoint = (name) =>
	name === ROOT ? [TRIGGER, 0] : [nodes[name].x + NODE_W, nodes[name].y + NODE_H / 2];
const inPoint = (name) => [nodes[name].x, nodes[name].y + NODE_H / 2];
function curve([x1, y1], [x2, y2]) {
	const dx = Math.max(60, Math.abs(x2 - x1) / 2);
	return `M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
}

function children() {
	const kids = {};
	for (const n of list.value) (kids[parentOf(n)] ||= []).push(n.name);
	for (const k in kids) kids[k].sort((a, b) => (nodes[a].y || 0) - (nodes[b].y || 0));
	return kids;
}

function fail(e) {
	toast.error(e?.messages?.[0] || e?.message || "That did not work.");
}

// --- Loading and layout ---------------------------------------------------------------------------

async function load() {
	const data = await call(API + "get_graph");
	for (const k of Object.keys(nodes)) if (!nodes[k].draft) delete nodes[k];
	for (const n of data.nodes) nodes[n.name] = n;
	suggestions.value = data.suggestions || {};
	gapCount.value = data.gaps || 0;
	const first = !data.nodes.some((n) => n.x != null);
	if (first && data.nodes.length) tidy(false);
	else placeNew();
}

function savePositions(names) {
	const positions = names
		.filter((n) => nodes[n] && !nodes[n].draft)
		.map((n) => ({ name: n, x: Math.round(nodes[n].x), y: Math.round(nodes[n].y) }));
	if (positions.length)
		call(API + "move_nodes", { positions: JSON.stringify(positions) }).catch(fail);
}

function tidy(save) {
	const kids = children();
	let slot = 0;
	const place = (name, depth) => {
		const n = nodes[name];
		const ch = kids[name] || [];
		let y;
		if (!ch.length) y = slot++ * ROW;
		else {
			const ys = ch.map((c) => place(c, depth + 1));
			y = (ys[0] + ys[ys.length - 1]) / 2;
		}
		n.x = ROOT_X + depth * COL;
		n.y = y;
		return y;
	};
	const ys = (kids[ROOT] || []).map((t) => place(t, 0));
	const shift = ys.length ? -(ys[0] + ys[ys.length - 1]) / 2 - NODE_H / 2 : 0;
	for (const n of list.value) n.y += shift;
	savePositions(Object.keys(nodes));
	if (save) nextTick(fit);
}

// The first free spot at (x, y) or below it, so a new node never lands on another one.
function freeSpot(x, y, skip) {
	const taken = (ty) =>
		list.value.some(
			(o) =>
				o.name !== skip &&
				o.x != null &&
				Math.abs(o.x - x) < NODE_W + 20 &&
				Math.abs(o.y - ty) < NODE_H + 12
		);
	while (taken(y)) y += ROW;
	return [x, y];
}

// A node with no saved place goes right of its parent, below its siblings.
function placeNew() {
	const placed = [];
	for (let pass = 0; pass < 20; pass++) {
		let left = false;
		for (const n of list.value) {
			if (n.x != null) continue;
			const parent = parentOf(n);
			const p = parent === ROOT ? null : nodes[parent];
			if (p && p.x == null) {
				left = true;
				continue;
			}
			const sibs = list.value.filter(
				(s) => s !== n && s.x != null && parentOf(s) === parent
			);
			[n.x, n.y] = freeSpot(
				p ? p.x + COL : ROOT_X,
				sibs.length ? Math.max(...sibs.map((s) => s.y)) + ROW : p ? p.y : -NODE_H / 2,
				n.name
			);
			placed.push(n.name);
		}
		if (!left) break;
	}
	savePositions(placed);
}

// --- View -----------------------------------------------------------------------------------------

function toWorld(ev) {
	const r = root.value.getBoundingClientRect();
	return [(ev.clientX - r.left - view.x) / view.k, (ev.clientY - r.top - view.y) / view.k];
}

function zoomAt(factor, cx, cy) {
	const r = root.value.getBoundingClientRect();
	cx = cx ?? r.width / 2;
	cy = cy ?? r.height / 2;
	const k = Math.min(2, Math.max(0.25, view.k * factor));
	view.x = cx - ((cx - view.x) * k) / view.k;
	view.y = cy - ((cy - view.y) * k) / view.k;
	view.k = k;
}

function fit() {
	if (!root.value) return;
	const r = root.value.getBoundingClientRect();
	const side = panel.value ? 380 : 0;
	let [x0, y0, x1, y1] = [0, -TRIGGER / 2, TRIGGER + 60, TRIGGER / 2 + 30];
	for (const n of list.value) {
		x0 = Math.min(x0, n.x);
		y0 = Math.min(y0, n.y - 14);
		x1 = Math.max(x1, n.x + NODE_W + 60);
		y1 = Math.max(y1, n.y + NODE_H);
	}
	const w = Math.max(200, r.width - side - 80);
	const h = Math.max(200, r.height - 110);
	const k = Math.min(1.2, Math.max(0.25, Math.min(w / (x1 - x0), h / (y1 - y0))));
	view.k = k;
	view.x = 40 + (w - (x1 - x0) * k) / 2 - x0 * k;
	view.y = 50 + (h - (y1 - y0) * k) / 2 - y0 * k;
}

function onWheel(ev) {
	if (ev.ctrlKey || ev.metaKey) {
		const r = root.value.getBoundingClientRect();
		zoomAt(Math.exp(-ev.deltaY * 0.01), ev.clientX - r.left, ev.clientY - r.top);
	} else {
		view.x -= ev.deltaX;
		view.y -= ev.deltaY;
	}
}

// --- Pointer: pan, drag a node, draw a wire ------------------------------------------------------

function track(ev, move, up) {
	const el = root.value;
	el.setPointerCapture(ev.pointerId);
	const onmove = (e) => move(e);
	const onup = (e) => {
		el.removeEventListener("pointermove", onmove);
		el.removeEventListener("pointerup", onup);
		el.removeEventListener("pointercancel", onup);
		up(e);
	};
	el.addEventListener("pointermove", onmove);
	el.addEventListener("pointerup", onup);
	el.addEventListener("pointercancel", onup);
}

function onPointerDown(ev) {
	if (ev.button !== 0) return;
	menu.value = null;
	const out = ev.target.closest("[data-out]");
	if (out) return startWire(ev, out.dataset.out);
	const node = ev.target.closest(".node");
	if (node) return startDrag(ev, node.dataset.name);
	if (ev.target.closest("[data-name]")) return; // Customer asks
	startPan(ev);
}

function startPan(ev) {
	const start = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y };
	let moved = false;
	panning.value = true;
	track(
		ev,
		(e) => {
			moved = moved || Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 3;
			view.x = start.vx + e.clientX - start.x;
			view.y = start.vy + e.clientY - start.y;
		},
		() => {
			panning.value = false;
			if (!moved) closePanel();
		}
	);
}

function startDrag(ev, name) {
	const n = nodes[name];
	const start = { x: ev.clientX, y: ev.clientY, nx: n.x, ny: n.y };
	let moved = false;
	track(
		ev,
		(e) => {
			const dx = (e.clientX - start.x) / view.k;
			const dy = (e.clientY - start.y) / view.k;
			moved = moved || Math.abs(dx) + Math.abs(dy) > 3;
			if (!moved) return;
			n.x = Math.round(start.nx + dx);
			n.y = Math.round(start.ny + dy);
		},
		() => (moved ? savePositions([name]) : openPanel(name))
	);
}

function startWire(ev, from) {
	temp.value = { from: outPoint(from), to: toWorld(ev) };
	track(
		ev,
		(e) => (temp.value = { ...temp.value, to: toWorld(e) }),
		(e) => {
			const [x, y] = temp.value.to;
			temp.value = null;
			const target = document.elementFromPoint(e.clientX, e.clientY)?.closest(".node");
			const to = target ? target.dataset.name : null;
			if (!to) openMenu(from, x, y - 20, [x, y - NODE_H / 2]);
			else if (to !== from) connect(to, from);
		}
	);
}

function hoverWire(child) {
	clearTimeout(wireTimer);
	const [x1, y1] = outPoint(parentOf(nodes[child]));
	const [x2, y2] = inPoint(child);
	wire.value = { child, x: (x1 + x2) / 2, y: (y1 + y2) / 2 };
}

function keepWire() {
	clearTimeout(wireTimer);
}

function leaveWire() {
	wireTimer = setTimeout(() => (wire.value = null), 250);
}

// --- Changes --------------------------------------------------------------------------------------

async function connect(child, parent) {
	const n = nodes[child];
	if (n.draft) return;
	if (!CHILDREN[typeOf(parent)].includes(n.node_type)) {
		return toast.warning(
			`A ${TYPES[n.node_type].label} cannot come after ${
				parent === ROOT ? "Customer asks" : TYPES[typeOf(parent)].label
			}.`
		);
	}
	try {
		Object.assign(n, await call(API + "connect", { child, parent }));
	} catch (e) {
		fail(e);
	}
}

function disconnect(child) {
	wire.value = null;
	if (!CHILDREN[ROOT].includes(nodes[child].node_type)) {
		return toast.warning(
			"A condition must come after a topic. Connect it to another node, or delete it."
		);
	}
	connect(child, ROOT);
}

function openMenu(from, x, y, at = null) {
	const r = root.value.getBoundingClientRect();
	menu.value = {
		from,
		at,
		left: Math.max(8, Math.min((x + 60) * view.k + view.x, r.width - 300)),
		top: Math.max(8, Math.min(y * view.k + view.y, r.height - 220)),
	};
}

function discardDrafts() {
	for (const k of Object.keys(nodes)) if (nodes[k].draft) delete nodes[k];
}

function addChild(parent, type, at, question) {
	menu.value = null;
	discardDrafts();
	const name = `new-${++drafts}`;
	const sibs = list.value.filter((s) => parentOf(s) === parent);
	const [px, py] = outPoint(parent);
	const [x, y] =
		at ||
		freeSpot(
			parent === ROOT ? ROOT_X : px + COL - NODE_W,
			sibs.length ? Math.max(...sibs.map((s) => s.y)) + ROW : py - NODE_H / 2
		);
	nodes[name] = {
		name,
		draft: true,
		node_type: type,
		parent: parent || "",
		question: question || "",
		answer: "",
		keywords: "",
		x,
		y,
	};
	openPanel(name);
}

const note = computed(() => {
	const n = editing.value;
	if (!n) return "";
	if (n.status === "Suggested" && n.node_type !== "Answer")
		return "The agent proposed this topic for an answer it learned. It is approved together with that answer.";
	if (n.status === "Suggested")
		return "The agent learned this from a rep's answer. It is used only after an approver accepts it (Suggestions).";
	if (!n.draft && n.node_type === "Answer")
		return n.last_used
			? `Last used in a reply ${fromNow(n.last_used)}`
			: "Not used in a reply yet";
	return "";
});
const labels = computed(() => {
	const t = editing.value?.node_type;
	if (t === "Topic")
		return { question: "Topic", questionHint: "For example: Delivery, Pricing, Returns" };
	if (t === "Condition")
		return {
			question: "Applies when",
			questionHint:
				"For example: Customer is in Chennai. The agent checks the chat and the CRM record.",
		};
	return { question: "Question", questionHint: "What the customer asks, in plain words" };
});
const panelTitle = computed(() =>
	panel.value?.kind === "gaps"
		? "Knowledge gaps"
		: editing.value?.draft
		? `New ${TYPES[editing.value.node_type].label}`
		: TYPES[editing.value?.node_type]?.label
);

function openPanel(name) {
	menu.value = null;
	for (const k of Object.keys(nodes)) if (nodes[k].draft && k !== name) delete nodes[k];
	const n = nodes[name];
	selected.value = name;
	editing.value = n;
	form.question = n.question || "";
	form.answer = n.answer || "";
	form.keywords = n.keywords || "";
	error.value = "";
	panel.value = { kind: "node" };
}

function closePanel() {
	panel.value = null;
	editing.value = null;
	selected.value = null;
	discardDrafts();
}

async function save() {
	const n = editing.value;
	error.value = "";
	if (!form.question.trim()) return (error.value = "Give it a name first.");
	if (n.node_type === "Answer" && !form.answer.trim())
		return (error.value = "Write the answer first.");
	saving.value = true;
	try {
		const saved = await call(API + "save_node", {
			node: JSON.stringify({
				name: n.draft ? null : n.name,
				node_type: n.node_type,
				parent_entry: n.parent || null,
				question: form.question.trim(),
				answer: form.answer.trim(),
				keywords: form.keywords.trim(),
				x: Math.round(n.x),
				y: Math.round(n.y),
			}),
		});
		delete nodes[n.name];
		nodes[saved.name] = { ...saved, x: n.x, y: n.y };
		closePanel();
		selected.value = saved.name;
		await load();
	} catch (e) {
		error.value = e?.messages?.[0] || e?.message || "Not saved.";
	} finally {
		saving.value = false;
	}
}

function countBelow(name) {
	const kids = children();
	const walk = (n) => (kids[n] || []).reduce((sum, c) => sum + 1 + walk(c), 0);
	return walk(name);
}

async function remove(name) {
	const n = nodes[name];
	if (!n || n.draft) return closePanel();
	const below = countBelow(name);
	const ok = await ask(
		below
			? `Delete "${n.question}" and the ${below} node(s) after it? The agent will stop using them.`
			: `Delete "${n.question}"? The agent will stop using it.`,
		{ title: "Delete this node?", confirmLabel: "Delete", danger: true }
	);
	if (!ok) return;
	try {
		await call(API + "remove_node", { name });
		closePanel();
		await load();
	} catch (e) {
		fail(e);
	}
}

async function showGaps() {
	closePanel();
	gaps.value = await call(API + "gaps");
	panel.value = { kind: "gaps" };
}

// --- Import a document ----------------------------------------------------------------------------

const parentOptions = computed(() => [
	{ label: "Customer asks", value: "" },
	...list.value
		.filter((n) => !n.draft && n.node_type !== "Answer")
		.map((n) => ({ label: `${TYPES[n.node_type].label}: ${n.question}`, value: n.name })),
]);

async function onFile(e) {
	const file = e.target.files?.[0];
	e.target.value = "";
	if (!file) return;
	importing.value = true;
	try {
		const formData = new FormData();
		formData.append("file", file, file.name);
		formData.append("is_private", "1");
		const r = await fetch("/api/method/upload_file", {
			method: "POST",
			body: formData,
			headers: { "X-Frappe-CSRF-Token": window.csrf_token },
		});
		const up = await r.json();
		if (!r.ok || !up.message) throw new Error("The file could not be uploaded.");
		const out = await call(API + "import_document", { file_url: up.message.file_url });
		if (out.error) throw new Error(out.error);
		imported.value = { ...out, file_name: file.name };
		picks.value = (out.entries || []).map((_, i) => i);
		const sel = selected.value && nodes[selected.value];
		importParent.value = sel && sel.node_type !== "Answer" && !sel.draft ? sel.name : "";
		importOpen.value = true;
	} catch (err) {
		fail(err);
	} finally {
		importing.value = false;
	}
}

async function confirmImport() {
	try {
		const r = await call(API + "confirm_import", {
			token: imported.value.token,
			picks: JSON.stringify(picks.value),
			parent: importParent.value || null,
		});
		importOpen.value = false;
		toast.success(`Added ${r.saved.length} answer(s)`);
		await load();
	} catch (e) {
		fail(e);
	}
}

// --- Keyboard -------------------------------------------------------------------------------------

function onKey(ev) {
	if (ev.target.closest("input, textarea, select, [contenteditable]")) return;
	if ((ev.key === "Delete" || ev.key === "Backspace") && selected.value) remove(selected.value);
	if (ev.key === "Escape") {
		menu.value = null;
		closePanel();
	}
}

onMounted(async () => {
	if (!boot.is_admin) return;
	await load();
	await nextTick();
	fit();
	window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<style>
.kb {
	--card: #ffffff;
	--line: #d9d8d4;
	--wire: #a8a7a2;
	--port: #8f8e89;
	--accent: #2a78d6;
	--accent-soft: #2a78d633;
}
@media (prefers-color-scheme: dark) {
	:root:where(:not([data-theme="light"])) .kb {
		--card: #232321;
		--line: #3a3a37;
		--wire: #6f6e69;
		--port: #8f8e89;
		--accent: #3987e5;
		--accent-soft: #3987e540;
	}
}
:root[data-theme="dark"] .kb {
	--card: #232321;
	--line: #3a3a37;
	--wire: #6f6e69;
	--port: #8f8e89;
	--accent: #3987e5;
	--accent-soft: #3987e540;
}
</style>
