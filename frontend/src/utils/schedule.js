import dayjs from "dayjs";

// Words for a scheduled message's timing (api/scheduled.py rows).
export const WEEKDAYS = [
	["monday", "Mon"],
	["tuesday", "Tue"],
	["wednesday", "Wed"],
	["thursday", "Thu"],
	["friday", "Fri"],
	["saturday", "Sat"],
	["sunday", "Sun"],
];

export function when(value) {
	if (!value) return "";
	const d = dayjs(value);
	const day = d.isSame(dayjs(), "day")
		? "Today"
		: d.isSame(dayjs().add(1, "day"), "day")
		? "Tomorrow"
		: d.format("ddd D MMM");
	return `${day}, ${d.format("h:mm A")}`;
}

// { interval, value, days, ends } (the dialog) or a row's repeat_* fields -> "Every Mon and Fri"
export function repeatText(r) {
	const interval = r.interval ?? r.repeat_interval;
	if (!interval) return "Once";
	const n = Number(r.value ?? r.repeat_value) || 1;
	const days = r.days ?? r.repeat_days ?? [];
	const ends = r.ends ?? r.repeat_ends;
	let text;
	if (interval === "day" && days.length && days.length < 7) {
		const names = WEEKDAYS.filter(([d]) => days.includes(d)).map(([, s]) => s);
		text = `Every ${
			names.length > 1 ? names.slice(0, -1).join(", ") + " and " + names.at(-1) : names[0]
		}`;
	} else {
		text = n === 1 ? `Every ${interval}` : `Every ${n} ${interval}s`;
	}
	return ends ? `${text}, until ${dayjs(ends).format("D MMM YYYY")}` : text;
}
