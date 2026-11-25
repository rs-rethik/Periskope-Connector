<template>
	<Teleport to="body">
		<Transition name="leave-fade">
			<div
				v-if="salesOne.leaving"
				class="sales-one-overlay fixed inset-0 z-[60] grid place-items-center"
				:style="{ '--x': `${salesOne.leaving.x}px`, '--y': `${salesOne.leaving.y}px` }"
				role="status"
				aria-live="polite"
			>
				<div class="sales-one-mark flex flex-col items-center gap-4">
					<span
						class="grid size-20 place-items-center rounded-[22px] bg-[#f2f5f9] shadow-xl"
					>
						<img :src="SALES_ONE_LOGO" alt="" class="size-14" />
					</span>
					<p class="text-base font-medium text-[#eef4ff]">Back to Sales One</p>
					<span class="sales-one-dots flex gap-1.5" aria-hidden="true"
						><span /><span /><span
					/></span>
				</div>
			</div>
		</Transition>
	</Teleport>
</template>

<script setup>
import { salesOne } from "@/stores/salesOne";

// served by Sales One at run time, not part of this build
const SALES_ONE_LOGO = "/assets/sales_one/images/logo.svg";
</script>

<style scoped>
/* The same move as Sales One's way in: a circle in Sales One's blue grows from the tapped button, the logo rises. */
.sales-one-overlay {
	background: radial-gradient(circle at var(--x) var(--y), #1673ea 0%, #0b4aa8 70%);
	animation: sales-one-grow 0.55s cubic-bezier(0.3, 0.7, 0.2, 1) both;
}
.sales-one-mark {
	animation: sales-one-rise 0.45s 0.2s cubic-bezier(0.2, 0.9, 0.3, 1.2) both;
}
.sales-one-dots span {
	width: 6px;
	height: 6px;
	border-radius: 9999px;
	background: #bcd6fb;
	animation: sales-one-dot 0.9s infinite ease-in-out;
}
.sales-one-dots span:nth-child(2) {
	animation-delay: 0.15s;
}
.sales-one-dots span:nth-child(3) {
	animation-delay: 0.3s;
}
@keyframes sales-one-grow {
	from {
		clip-path: circle(0 at var(--x) var(--y));
	}
	to {
		clip-path: circle(150% at var(--x) var(--y));
	}
}
@keyframes sales-one-rise {
	from {
		opacity: 0;
		transform: translateY(16px) scale(0.85);
	}
	to {
		opacity: 1;
		transform: none;
	}
}
@keyframes sales-one-dot {
	0%,
	80%,
	100% {
		opacity: 0.35;
		transform: scale(0.8);
	}
	40% {
		opacity: 1;
		transform: scale(1);
	}
}
.leave-fade-leave-active {
	transition: opacity 0.2s ease;
}
.leave-fade-leave-to {
	opacity: 0;
}
@media (prefers-reduced-motion: reduce) {
	.sales-one-overlay,
	.sales-one-mark,
	.sales-one-dots span {
		animation: none;
	}
}
</style>
