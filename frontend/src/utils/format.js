import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";

dayjs.extend(relativeTime);

export function listTime(value) {
	if (!value) return "";
	const d = dayjs(value);
	if (d.isSame(dayjs(), "day")) return d.format("h:mm A");
	if (d.isSame(dayjs().subtract(1, "day"), "day")) return "Yesterday";
	if (d.isSame(dayjs(), "year")) return d.format("D MMM");
	return d.format("D MMM YYYY");
}

export function messageTime(value) {
	return value ? dayjs(value).format("h:mm A") : "";
}

export function dayLabel(value) {
	const d = dayjs(value);
	if (d.isSame(dayjs(), "day")) return "Today";
	if (d.isSame(dayjs().subtract(1, "day"), "day")) return "Yesterday";
	return d.format(d.isSame(dayjs(), "year") ? "dddd, D MMMM" : "D MMMM YYYY");
}

export function sameDay(a, b) {
	return a && b && dayjs(a).isSame(dayjs(b), "day");
}

export function fromNow(value) {
	return value ? dayjs(value).fromNow() : "";
}

export function initials(name) {
	return (
		(name || "?")
			.replace(/[^\p{L}\p{N} ]/gu, "")
			.split(" ")
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0])
			.join("")
			.toUpperCase() || "#"
	);
}

// A readable name from a user id (an email): "ravi.kumar@example.com" is "Ravi Kumar".
export function nameFromEmail(user) {
	return (user || "")
		.split("@")[0]
		.replace(/[._]/g, " ")
		.replace(/\b\w/g, (c) => c.toUpperCase());
}

// http(s) or www. links in a message; trailing punctuation stays text ("see example.com." → the dot)
const LINK = /\b(?:https?:\/\/|www\.)[^\s<>"]+/gi;
const TRAILING = /[.,;:!?)\]}'"]+$/;

// The text split into plain parts and links, for rendering links without v-html.
export function linkParts(text) {
	const parts = [];
	let last = 0;
	for (const match of (text || "").matchAll(LINK)) {
		const link = match[0].replace(TRAILING, "");
		if (match.index > last) parts.push({ text: text.slice(last, match.index) });
		const href = /^www\./i.test(link) ? `https://${link}` : link;
		parts.push({ text: link, href });
		last = match.index + link.length;
	}
	if (last < (text || "").length) parts.push({ text: text.slice(last) });
	return parts;
}
