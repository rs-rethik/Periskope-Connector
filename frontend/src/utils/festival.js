import { computed, watchEffect } from "vue";
import { boot } from "@/boot";

// Today's festival (ai/festival.py) themes the whole app: <html data-festival="<look>"> switches the
// colours in theme.css.

// The looks the AI can pick from: banner colours, the string of lights and the motif.
export const LOOKS = {
	diwali: {
		from: "#7c2d12",
		to: "#d97706",
		lights: ["#fde68a", "#fb923c", "#fef3c7"],
		motif: "diya",
	},
	pongal: {
		from: "#166534",
		to: "#ca8a04",
		lights: ["#fef08a", "#bbf7d0", "#fdba74"],
		motif: "sun",
	},
	tamil_new_year: {
		from: "#9a3412",
		to: "#ca8a04",
		lights: ["#fef08a", "#fecaca"],
		motif: "flower",
	},
	ayudha_pooja: {
		from: "#7f1d1d",
		to: "#ea580c",
		lights: ["#fde68a", "#fecaca"],
		motif: "diya",
	},
	christmas: {
		from: "#14532d",
		to: "#b91c1c",
		lights: ["#fde047", "#fca5a5", "#bbf7d0"],
		motif: "star",
	},
	new_year: {
		from: "#312e81",
		to: "#a21caf",
		lights: ["#f5d0fe", "#c7d2fe", "#fde68a"],
		motif: "sparkle",
	},
	holi: {
		from: "#be185d",
		to: "#7c3aed",
		lights: ["#fde047", "#86efac", "#7dd3fc", "#f9a8d4"],
		motif: "flower",
	},
	eid: { from: "#064e3b", to: "#0f766e", lights: ["#fef3c7", "#a7f3d0"], motif: "moon" },
	national: {
		from: "#c2410c",
		to: "#15803d",
		lights: ["#fed7aa", "#ffffff", "#bbf7d0"],
		motif: "star",
	},
	festive: { from: "#9d174d", to: "#d97706", lights: ["#fde68a", "#fbcfe8"], motif: "sparkle" },
};

export const motifs = {
	diya: `<path d="M4 19c2 6 7 8 12 8s10-2 12-8z" fill="#fde68a"/><path d="M16 4c3 4 4 7 0 11-4-4-3-7 0-11z" fill="#fb923c"/><path d="M16 8c1.5 2 2 3.5 0 5.5-2-2-1.5-3.5 0-5.5z" fill="#fef3c7"/>`,
	sun: `<circle cx="16" cy="16" r="6" fill="#fde047"/><g stroke="#fef08a" stroke-width="2" stroke-linecap="round"><path d="M16 3v4M16 25v4M3 16h4M25 16h4M6.8 6.8l2.8 2.8M22.4 22.4l2.8 2.8M6.8 25.2l2.8-2.8M22.4 9.6l2.8-2.8"/></g>`,
	flower: `<g fill="#fde68a"><circle cx="16" cy="8" r="5"/><circle cx="24" cy="16" r="5"/><circle cx="16" cy="24" r="5"/><circle cx="8" cy="16" r="5"/></g><circle cx="16" cy="16" r="4" fill="#fb7185"/>`,
	star: `<path d="M16 3l3.7 8.3 9 .9-6.8 6 2 8.8L16 22.4 8.1 27l2-8.8-6.8-6 9-.9z" fill="#fde047"/>`,
	sparkle: `<path d="M16 2c1 8 4 11 12 14-8 3-11 6-12 14-1-8-4-11-12-14 8-3 11-6 12-14z" fill="#fef3c7"/>`,
	moon: `<path d="M21 4a12 12 0 1 0 7 19A10 10 0 0 1 21 4z" fill="#fef3c7"/><path d="M25 7l.8 1.8 2 .2-1.5 1.3.5 2-1.8-1-1.8 1 .5-2L22.2 9l2-.2z" fill="#fde68a"/>`,
};

export const festival = computed(() => {
	const f = boot.festival;
	return f || null;
});

// The browser tab's icon on a festival day: the brand logo as it is, with the festival's motif in the
// bottom-right corner (the sidebar draws the same in BrandLogo.vue). Falls back to the plain logo.
export async function festivalIcon(logoUrl) {
	const l = festival.value && LOOKS[festival.value.look];
	if (!l) return logoUrl;
	const load = (src) =>
		new Promise((resolve, reject) => {
			const img = new Image();
			img.onload = () => resolve(img);
			img.onerror = reject;
			img.src = src;
		});
	try {
		const mark = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">${
			motifs[l.motif]
		}</svg>`;
		const [logo, motif] = await Promise.all([
			load(logoUrl),
			load(`data:image/svg+xml,${encodeURIComponent(mark)}`),
		]);
		const canvas = document.createElement("canvas");
		canvas.width = canvas.height = 64;
		const ctx = canvas.getContext("2d");
		ctx.drawImage(logo, 0, 0, 64, 64);
		ctx.shadowColor = "rgba(0, 0, 0, 0.5)";
		ctx.shadowBlur = 3;
		ctx.drawImage(motif, 30, 30, 36, 36);
		return canvas.toDataURL("image/png");
	} catch {
		return logoUrl;
	}
}

export function setFestival(f) {
	boot.festival = f || null;
}

watchEffect(() => {
	const root = document.documentElement;
	if (festival.value) root.dataset.festival = festival.value.look;
	else delete root.dataset.festival;
});
