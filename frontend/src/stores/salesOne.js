import { reactive } from "vue";

// The way back to Sales One, the sales app that opens this inbox (boot.sales_one says who may use it).
const SALES_ONE = "/sales/";
const ANIMATION_MS = 700;

// where the animation starts ({ x, y }) while it plays; only in the installed app
export const salesOne = reactive({ leaving: null });

function isInstalledApp() {
	return (
		window.matchMedia("(display-mode: standalone)").matches ||
		window.navigator.standalone === true
	);
}

export function backToSalesOne(event = null) {
	if (!isInstalledApp()) {
		window.location.href = SALES_ONE;
		return;
	}
	const rect = event?.currentTarget?.getBoundingClientRect?.();
	salesOne.leaving = {
		x: rect ? rect.left + rect.width / 2 : window.innerWidth / 2,
		y: rect ? rect.top + rect.height / 2 : 40,
	};
	const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	setTimeout(() => (window.location.href = SALES_ONE), reduced ? 150 : ANIMATION_MS);
}

// back in this app from the browser's cache: the animation must not still be on screen
window.addEventListener("pageshow", () => (salesOne.leaving = null));
