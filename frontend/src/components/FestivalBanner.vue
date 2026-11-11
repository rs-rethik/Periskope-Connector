<template>
	<div
		v-if="festival"
		class="festival relative overflow-hidden rounded-2xl px-5 py-4 text-white shadow-sm"
		:style="{ background: look.background }"
	>
		<!-- a string of lights along the top -->
		<div class="lights pointer-events-none absolute inset-x-0 top-0 flex justify-around">
			<span
				v-for="i in 18"
				:key="i"
				class="light"
				:style="{
					background: look.lights[i % look.lights.length],
					animationDelay: `${(i % 6) * 0.35}s`,
				}"
			/>
		</div>
		<!-- the look's motif, repeated on the right -->
		<div
			class="pointer-events-none absolute inset-y-0 right-0 hidden items-center gap-5 pr-14 opacity-90 sm:flex"
		>
			<svg
				v-for="(size, i) in [26, 38, 30]"
				:key="i"
				:width="size"
				:height="size"
				viewBox="0 0 32 32"
				class="motif"
				:style="{ animationDelay: `${i * 0.6}s` }"
			>
				<g v-html="motifs[look.motif]" />
			</svg>
		</div>
		<div class="relative flex items-start gap-3">
			<div class="min-w-0">
				<p class="text-xs font-semibold uppercase tracking-[0.14em] text-white/75">
					{{ festival.festival }}
				</p>
				<p class="mt-0.5 text-lg font-semibold leading-snug">{{ festival.greeting }}</p>
			</div>
		</div>
	</div>
</template>

<script setup>
import { computed } from "vue";
import { LOOKS, festival, motifs } from "@/utils/festival";

// Today's festival greeting, as the AI found it on the web (ai/festival.py). It stays up all day:
// there is no close button.

const look = computed(() => {
	const l = LOOKS[festival.value?.look] || LOOKS.festive;
	return { ...l, background: `linear-gradient(115deg, ${l.from}, ${l.to})` };
});
</script>

<style scoped>
.light {
	width: 7px;
	height: 7px;
	margin-top: 6px;
	border-radius: 9999px;
	box-shadow: 0 0 8px 2px currentColor;
	color: rgb(255 255 255 / 0.35);
	animation: twinkle 2.4s ease-in-out infinite;
}
.motif {
	filter: drop-shadow(0 0 6px rgb(255 255 255 / 0.35));
	animation: float 4s ease-in-out infinite;
}
@keyframes twinkle {
	50% {
		opacity: 0.35;
	}
}
@keyframes float {
	50% {
		transform: translateY(-4px);
	}
}
@media (prefers-reduced-motion: reduce) {
	.light,
	.motif {
		animation: none;
	}
}
</style>
