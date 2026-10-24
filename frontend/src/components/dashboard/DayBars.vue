<template>
	<div class="viz-root">
		<div class="mb-2 flex items-center gap-4 text-xs text-ink-gray-6">
			<span v-for="s in series" :key="s.key" class="inline-flex items-center gap-1.5">
				<span class="size-2.5 rounded-sm" :style="{ background: `var(${s.color})` }" />{{
					s.label
				}}
			</span>
			<button
				class="ml-auto text-ink-gray-5 hover:text-ink-gray-8"
				@click="asTable = !asTable"
			>
				{{ asTable ? "Show chart" : "Show table" }}
			</button>
		</div>
		<table v-if="asTable" class="w-full text-sm">
			<thead class="text-left text-xs text-ink-gray-5">
				<tr>
					<th class="py-1">Day</th>
					<th v-for="s in series" :key="s.key" class="py-1 text-right">{{ s.label }}</th>
				</tr>
			</thead>
			<tbody>
				<tr v-for="d in days" :key="d.day" class="border-t border-outline-gray-1">
					<td class="py-1 text-ink-gray-7">{{ dayName(d.day) }}</td>
					<td v-for="s in series" :key="s.key" class="py-1 text-right text-ink-gray-8">
						{{ d[s.key] }}
					</td>
				</tr>
			</tbody>
		</table>
		<div v-else class="relative" @mouseleave="hover = null">
			<svg
				:viewBox="`0 0 ${W} ${H}`"
				class="h-56 w-full"
				preserveAspectRatio="none"
				role="img"
				:aria-label="title"
			>
				<!-- recessive grid -->
				<g v-for="t in ticks" :key="t">
					<line
						:x1="PAD_L"
						:x2="W"
						:y1="y(t)"
						:y2="y(t)"
						class="stroke-[var(--grid)]"
						stroke-width="1"
					/>
					<text
						:x="PAD_L - 6"
						:y="y(t) + 4"
						text-anchor="end"
						class="fill-[var(--text-secondary)] text-[11px]"
					>
						{{ t }}
					</text>
				</g>
				<g v-for="(d, i) in days" :key="d.day">
					<!-- hit target: the whole day column -->
					<rect
						:x="x(i)"
						y="0"
						:width="band"
						:height="H - PAD_B"
						fill="transparent"
						@mouseenter="hover = i"
					/>
					<path
						v-for="(s, j) in series"
						:key="s.key"
						:d="bar(x(i) + inner + j * (barW + GAP), barW, d[s.key])"
						:style="{ fill: `var(${s.color})` }"
						:opacity="hover === null || hover === i ? 1 : 0.45"
						pointer-events="none"
					/>
					<text
						v-if="showLabel(i)"
						:x="x(i) + band / 2"
						:y="H - 6"
						text-anchor="middle"
						class="fill-[var(--text-secondary)] text-[11px]"
					>
						{{ shortDay(d.day) }}
					</text>
				</g>
				<line
					:x1="PAD_L"
					:x2="W"
					:y1="H - PAD_B"
					:y2="H - PAD_B"
					class="stroke-[var(--axis)]"
					stroke-width="1"
				/>
			</svg>
			<div
				v-if="hover !== null"
				class="pointer-events-none absolute top-2 z-10 rounded-lg bg-surface-base px-3 py-2 text-xs shadow-lg ring-1 ring-outline-gray-2"
				:style="{ left: `min(${((x(hover) + band) / W) * 100}%, calc(100% - 10rem))` }"
			>
				<div class="mb-1 font-medium text-ink-gray-8">{{ dayName(days[hover].day) }}</div>
				<div
					v-for="s in series"
					:key="s.key"
					class="flex items-center gap-2 text-ink-gray-7"
				>
					<span class="size-2 rounded-sm" :style="{ background: `var(${s.color})` }" />{{
						s.label
					}}
					<span class="ml-auto pl-4 font-medium text-ink-gray-9">{{
						days[hover][s.key]
					}}</span>
				</div>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";
import dayjs from "dayjs";

// Grouped bars per day (magnitude over time, two series). Colours: categorical slots 1 and 2 of the
// reference palette, validated for CVD in light and dark (see Dashboard.vue's .viz-root tokens).
const props = defineProps({
	days: { type: Array, default: () => [] },
	title: { type: String, default: "" },
});
const series = [
	{ key: "received", label: "Received", color: "--series-1" },
	{ key: "sent", label: "Sent", color: "--series-2" },
];
const W = 720;
const H = 220;
const PAD_L = 34;
const PAD_B = 22;
const GAP = 2;
const asTable = ref(false);
const hover = ref(null);

const max = computed(() => Math.max(1, ...props.days.flatMap((d) => series.map((s) => d[s.key]))));
const niceMax = computed(() => {
	const m = max.value;
	const step = Math.pow(10, Math.floor(Math.log10(m)));
	return Math.ceil(m / step) * step;
});
const ticks = computed(() => [0, Math.round(niceMax.value / 2), niceMax.value]);
const band = computed(() => (W - PAD_L) / Math.max(1, props.days.length));
const barW = computed(() => Math.max(2, Math.min(14, (band.value * 0.7 - GAP) / series.length)));
const inner = computed(() => (band.value - (barW.value * series.length + GAP)) / 2);

function x(i) {
	return PAD_L + i * band.value;
}
function y(v) {
	return H - PAD_B - (v / niceMax.value) * (H - PAD_B - 8);
}
// A bar anchored to the baseline with a 4px rounded data end.
function bar(left, width, value) {
	const top = y(value);
	const bottom = H - PAD_B;
	if (bottom - top < 0.5) return "";
	const r = Math.min(4, width / 2, bottom - top);
	return `M${left},${bottom} V${top + r} Q${left},${top} ${left + r},${top} H${
		left + width - r
	} Q${left + width},${top} ${left + width},${top + r} V${bottom} Z`;
}
function showLabel(i) {
	const every = Math.ceil(props.days.length / 10);
	return i % every === 0;
}
function shortDay(d) {
	return dayjs(d).format(props.days.length > 14 ? "D MMM" : "ddd D");
}
function dayName(d) {
	return dayjs(d).format("dddd, D MMM");
}
</script>
