<template>
	<span class="relative inline-flex shrink-0">
		<img :src="boot.brand?.logo || logo" class="rounded object-contain" alt="" />
		<!-- on a festival day the logo keeps its look and wears the festival's motif in a corner -->
		<svg
			v-if="motif"
			viewBox="0 0 32 32"
			class="festival-mark absolute -bottom-1 -right-1 size-[55%]"
			aria-hidden="true"
		>
			<g v-html="motif" />
		</svg>
	</span>
</template>

<script setup>
import { computed } from "vue";
import { boot } from "@/boot";
import { LOOKS, festival, motifs } from "@/utils/festival";

const logo = "/assets/periskope_connector/images/logo.svg";
const motif = computed(() => {
	const l = festival.value && LOOKS[festival.value.look];
	return l ? motifs[l.motif] : "";
});
</script>

<style scoped>
img {
	width: 100%;
	height: 100%;
}
.festival-mark {
	filter: drop-shadow(0 0 1.5px rgb(0 0 0 / 0.45)) drop-shadow(0 0 4px rgb(251 146 60 / 0.8));
}
</style>
