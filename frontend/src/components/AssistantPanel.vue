<template>
	<Teleport to="body">
		<!-- The floating Assistant button, like Jarvis: drag it up or down, or to the other side; it
		     snaps to the nearest edge and stays where it was left. -->
		<button
			v-show="!(open && narrow)"
			type="button"
			class="assistant-fab"
			:class="{ dragging: drag.moved, open, 'in-chat': !!route.params.chat }"
			:style="{ top: `${top}px`, [fab.side]: `${EDGE}px` }"
			:aria-label="open ? 'Close the Assistant' : 'Ask the Assistant'"
			:aria-expanded="open ? 'true' : 'false'"
			:title="open ? '' : 'Ask the Assistant (drag to move)'"
			@pointerdown="onDown"
			@pointermove="onMove"
			@pointerup="onUp"
			@pointercancel="onUp"
		>
			<LucideX v-if="open" class="size-6" />
			<AskIcon v-else class="ask-icon size-7" />
		</button>

		<Transition name="pop">
			<aside
				v-if="open"
				class="fixed z-50 flex flex-col overflow-hidden border-outline-gray-2 bg-surface-base shadow-2xl"
				:class="narrow ? 'safe-top safe-bottom inset-0' : 'rounded-2xl border'"
				:style="panelStyle"
				role="dialog"
				aria-label="Assistant"
				@keydown.esc="open = false"
			>
				<header
					class="flex shrink-0 items-center gap-2.5 border-b border-outline-gray-1 px-4 py-3"
				>
					<span
						class="assistant-mark grid size-8 shrink-0 place-items-center rounded-full"
					>
						<AskIcon class="size-5" />
					</span>
					<div class="min-w-0 flex-1">
						<p class="text-base font-semibold text-ink-gray-9">Assistant</p>
						<p class="truncate text-xs text-ink-gray-5">
							Ask how to do something, or for a report to download
						</p>
					</div>
					<Button
						v-if="bot.turns.length"
						variant="ghost"
						tooltip="New conversation"
						aria-label="New conversation"
						@click="reset"
					>
						<template #icon><LucideRotateCcw class="size-4" /></template>
					</Button>
					<Button
						variant="ghost"
						tooltip="Close"
						aria-label="Close"
						@click="open = false"
					>
						<template #icon><LucideX class="size-4" /></template>
					</Button>
				</header>

				<div ref="scroller" class="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
					<div v-if="!bot.turns.length" class="pt-2">
						<p class="text-sm text-ink-gray-7">
							Hi {{ firstName }}! I can answer basic questions about this app and
							your inbox, and download reports for you.
						</p>
						<p
							class="mt-4 text-xs font-medium uppercase tracking-wide text-ink-gray-5"
						>
							Try asking
						</p>
						<div class="mt-2 flex flex-col items-start gap-1.5">
							<button
								v-for="s in suggestions"
								:key="s"
								class="rounded-full border border-outline-gray-2 px-3 py-1.5 text-left text-sm text-ink-gray-8 transition hover:bg-surface-gray-2"
								@click="send(s)"
							>
								{{ s }}
							</button>
						</div>
					</div>

					<div
						v-for="(t, i) in bot.turns"
						:key="i"
						class="flex"
						:class="t.role === 'user' ? 'justify-end' : 'justify-start'"
					>
						<div
							class="max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed"
							:class="
								t.role === 'user'
									? 'rounded-br-md bg-surface-gray-7 text-ink-white'
									: t.error
									? 'rounded-bl-md bg-surface-red-1 text-ink-red-8'
									: 'rounded-bl-md bg-surface-gray-2 text-ink-gray-9'
							"
						>
							<div class="bot-text" v-html="render(t.text)" />
							<a
								v-if="t.download"
								:href="t.download.url"
								class="mt-2.5 flex items-center gap-2.5 rounded-xl border border-outline-gray-2 bg-surface-base px-3 py-2 text-ink-gray-9 no-underline transition hover:border-outline-gray-3 hover:bg-surface-gray-1"
								download
							>
								<span
									class="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-green-2 text-ink-green-7"
								>
									<LucideFileSpreadsheet class="size-4" />
								</span>
								<span class="min-w-0 flex-1">
									<span class="block truncate text-sm font-medium">{{
										t.download.label
									}}</span>
									<span class="block text-xs text-ink-gray-5">{{
										period(t.download)
									}}</span>
								</span>
								<LucideDownload class="size-4 shrink-0 text-ink-gray-6" />
							</a>
						</div>
					</div>

					<div v-if="bot.busy" class="flex justify-start">
						<div
							class="flex gap-1 rounded-2xl rounded-bl-md bg-surface-gray-2 px-4 py-3"
						>
							<span
								v-for="d in 3"
								:key="d"
								class="dot"
								:style="{ animationDelay: `${d * 0.15}s` }"
							/>
						</div>
					</div>
				</div>

				<form
					class="flex shrink-0 items-end gap-2 border-t border-outline-gray-1 p-3"
					@submit.prevent="send()"
				>
					<textarea
						ref="input"
						v-model="draft"
						rows="1"
						maxlength="1000"
						placeholder="Ask a question…"
						class="max-h-32 min-h-9 flex-1 resize-none rounded-lg border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-9 placeholder:text-ink-gray-4 max-md:text-[16px] focus:border-outline-gray-4 focus:ring-0"
						@keydown.enter.exact.prevent="send()"
						@input="grow"
					/>
					<Button
						variant="solid"
						type="submit"
						:disabled="!draft.trim() || bot.busy"
						aria-label="Send"
						class="!h-9"
					>
						<template #icon><LucideSendHorizontal class="size-4" /></template>
					</Button>
				</form>
			</aside>
		</Transition>
	</Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { Button, call } from "frappe-ui";
import dayjs from "dayjs";
import AskIcon from "@/components/AskIcon.vue";
import LucideX from "~icons/lucide/x";
import LucideRotateCcw from "~icons/lucide/rotate-ccw";
import LucideSendHorizontal from "~icons/lucide/send-horizontal";
import LucideDownload from "~icons/lucide/download";
import LucideFileSpreadsheet from "~icons/lucide/file-spreadsheet";
import { boot } from "@/boot";
import { errorText } from "@/utils/errors";
import { assistant as bot } from "@/stores/assistant";

// The Assistant (api/help_bot.py): basic questions about the app and the user's inbox, and report
// downloads. Mounted once in App.vue, with its floating button on every page.

const open = computed({
	get: () => bot.open,
	set: (v) => (bot.open = v),
});
const route = useRoute();
const draft = ref("");
const scroller = ref(null);
const input = ref(null);

// --- the floating button and where the panel opens beside it ---
const SIZE = 54; // the button
const EDGE = 20; // its gap from the screen edge
const GAP = 12; // between the button and the panel
const PANEL_W = 400;
const PANEL_H = 600;
const KEY = "periskope-assistant-fab";

const view = reactive({ w: window.innerWidth, h: window.innerHeight });
const narrow = computed(() => view.w < 640);
const fab = reactive(saved() || { side: "right", y: view.h - SIZE - 96 });
const drag = reactive({ active: false, moved: false, startX: 0, startY: 0, y0: 0 });

function saved() {
	try {
		const v = JSON.parse(localStorage.getItem(KEY) || "null");
		return v && ["left", "right"].includes(v.side) && typeof v.y === "number" ? v : null;
	} catch {
		return null;
	}
}

function clampY(y) {
	return Math.min(Math.max(y, EDGE), view.h - SIZE - EDGE);
}
// Where the button shows: its saved place, kept on the screen. Only the shown place moves when the
// window shrinks (a phone's keyboard), so the button is back where it was left afterwards.
const top = computed(() => clampY(fab.y));

function onDown(e) {
	if (e.button !== 0) return;
	Object.assign(drag, {
		active: true,
		moved: false,
		startX: e.clientX,
		startY: e.clientY,
		y0: top.value,
	});
	e.currentTarget.setPointerCapture(e.pointerId);
}

function onMove(e) {
	if (!drag.active) return;
	const dx = e.clientX - drag.startX;
	const dy = e.clientY - drag.startY;
	if (!drag.moved && Math.hypot(dx, dy) < 5) return;
	drag.moved = true;
	fab.y = clampY(drag.y0 + dy);
	fab.side = e.clientX < view.w / 2 ? "left" : "right";
}

function onUp() {
	if (!drag.active) return;
	drag.active = false;
	if (drag.moved) {
		try {
			localStorage.setItem(KEY, JSON.stringify({ side: fab.side, y: fab.y }));
		} catch {
			// private window: the place is kept for this visit only
		}
		setTimeout(() => (drag.moved = false));
	} else {
		open.value = !open.value;
	}
}

// Beside the button, on the inside, its bottom level with the button's, kept on the screen.
const panelStyle = computed(() => {
	if (narrow.value) return {};
	const h = Math.min(PANEL_H, view.h - 2 * GAP);
	const panelTop = Math.min(Math.max(top.value + SIZE - h, GAP), view.h - h - GAP);
	return {
		top: `${panelTop}px`,
		[fab.side]: `${EDGE + SIZE + GAP}px`,
		width: `${PANEL_W}px`,
		height: `${h}px`,
	};
});

function onResize() {
	view.w = window.innerWidth;
	view.h = window.innerHeight;
}
onMounted(() => window.addEventListener("resize", onResize));
onBeforeUnmount(() => window.removeEventListener("resize", onResize));

const firstName = computed(() => (boot.full_name || "").split(" ")[0] || "there");
const suggestions = computed(() => [
	"How do I assign a chat to someone?",
	"How many chats are waiting for a reply?",
	...(boot.is_manager
		? ["Download the response time report for last week", "Get the open escalations as CSV"]
		: ["What does the SLA badge on a chat mean?"]),
]);

async function send(text) {
	const question = (text ?? draft.value).trim();
	if (!question || bot.busy) return;
	const history = bot.turns.filter((t) => !t.error).map((t) => ({ role: t.role, text: t.text }));
	bot.turns.push({ role: "user", text: question });
	draft.value = "";
	grow();
	bot.busy = true;
	scrollDown();
	try {
		const r = await call("periskope_connector.api.help_bot.ask", { question, history });
		bot.turns.push({ role: "assistant", text: r.reply, download: r.download });
	} catch (e) {
		bot.turns.push({
			role: "assistant",
			text: errorText(e, "I could not answer just now. Please try again."),
			error: true,
		});
	} finally {
		bot.busy = false;
		scrollDown();
		nextTick(() => input.value?.focus());
	}
}

function reset() {
	bot.turns = [];
	nextTick(() => input.value?.focus());
}

function scrollDown() {
	nextTick(() =>
		scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: "smooth" })
	);
}

function grow() {
	const el = input.value;
	if (!el) return;
	el.style.height = "auto";
	el.style.height = `${el.scrollHeight}px`;
}

function period(d) {
	const kind = d.format === "csv" ? "CSV" : "Excel";
	if (!d.from_date) return `${kind} · as of today`;
	const f = (v) => dayjs(v).format("D MMM YYYY");
	return `${kind} · ${f(d.from_date)} – ${f(d.to_date)}`;
}

// Plain text with **bold**, "-" bullets and line breaks; everything else is escaped.
function render(text) {
	const esc = (text || "")
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
	return esc
		.split("\n")
		.map((line) =>
			/^\s*[-•]\s+/.test(line)
				? `<span class="bullet">${line.replace(/^\s*[-•]\s+/, "")}</span>`
				: line
		)
		.join("<br>")
		.replace(/<\/span><br>/g, "</span>");
}

watch(open, (v) => {
	if (v)
		nextTick(() => {
			input.value?.focus();
			scrollDown();
		});
});
</script>

<style scoped>
.bot-text :deep(.bullet) {
	display: block;
	position: relative;
	padding-left: 0.9rem;
}
.bot-text :deep(.bullet)::before {
	content: "•";
	position: absolute;
	left: 0.1rem;
}
.dot {
	width: 6px;
	height: 6px;
	border-radius: 9999px;
	background: currentColor;
	color: var(--ink-gray-4, #a3a3a3);
	animation: bounce 1s ease-in-out infinite;
}
@keyframes bounce {
	50% {
		transform: translateY(-3px);
		opacity: 0.5;
	}
}
/* Round, in the app's own colour (Settings > Branding, or the festival's on a festival day: the
   same colour as the solid buttons), with the chat-bubble mark. */
.assistant-fab {
	position: fixed;
	z-index: 51;
	display: grid;
	place-items: center;
	width: 56px;
	height: 56px;
	border-radius: 9999px;
	color: var(--surface-gray-9);
	background: var(--surface-gray-9);
	box-shadow: 0 8px 22px -6px color-mix(in oklch, var(--surface-gray-9) 70%, transparent),
		inset 0 0 0 1px rgb(255 255 255 / 0.12);
	cursor: grab;
	touch-action: none;
	transition: top 0.2s ease, transform 0.15s ease;
}
/* A phone has no spare corner: the button is hidden where it would cover the page (an open chat,
   a dialog, the CRM panel, the image viewer); the menu drawer has an Assistant entry instead. */
@media (max-width: 767px) {
	.assistant-fab.in-chat,
	body:has([role="dialog"]) .assistant-fab {
		display: none;
	}
}
.assistant-fab:hover {
	transform: scale(1.05);
}
.assistant-fab:hover .ask-icon {
	animation: nod 0.6s ease;
}
.assistant-fab.open {
	color: #fff;
}
.assistant-fab:focus-visible {
	outline: 2px solid var(--surface-gray-9);
	outline-offset: 3px;
}
.assistant-fab.dragging {
	cursor: grabbing;
	transition: none;
	transform: scale(1.08);
}
.assistant-mark {
	color: var(--surface-gray-9);
	background: var(--surface-gray-9);
}
@keyframes nod {
	30% {
		transform: rotate(-8deg);
	}
	60% {
		transform: rotate(5deg);
	}
}
.pop-enter-active,
.pop-leave-active {
	transition: transform 0.18s ease, opacity 0.18s ease;
}
.pop-enter-from,
.pop-leave-to {
	transform: translateY(8px) scale(0.98);
	opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
	.dot,
	.assistant-fab,
	.ask-icon,
	.pop-enter-active,
	.pop-leave-active {
		animation: none;
		transition: none;
	}
}
</style>
