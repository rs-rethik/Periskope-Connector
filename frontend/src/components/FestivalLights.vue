<template>
	<div
		v-if="festival"
		class="pointer-events-none fixed inset-x-0 top-0 z-30 flex h-3 gap-7 overflow-hidden px-3"
		aria-hidden="true"
	>
		<span
			v-for="i in 80"
			:key="i"
			class="bulb"
			:style="{
				background: colours[i % colours.length],
				color: colours[i % colours.length],
				animationDelay: `${(i % 7) * 0.4}s`,
			}"
		/>
	</div>
</template>

<script setup>
import { computed } from "vue";
import { LOOKS, festival } from "@/utils/festival";

// A string of festival lights along the top of every page while the festival theme is on.
const colours = computed(() => {
	const l = LOOKS[festival.value?.look] || LOOKS.festive;
	return [l.to, l.from, l.to, l.lights[0]];
});
</script>

<style scoped>
.bulb {
	width: 5px;
	height: 5px;
	flex-shrink: 0;
	margin-top: 3px;
	border-radius: 9999px;
	box-shadow: 0 0 6px 1px currentColor;
	animation: glow 2.8s ease-in-out infinite;
}
@keyframes glow {
	50% {
		opacity: 0.3;
	}
}
@media (prefers-reduced-motion: reduce) {
	.bulb {
		animation: none;
	}
}
</style>
