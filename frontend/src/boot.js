import { reactive } from "vue";

// What www/whatsapp.py put on the page: the user, their role and their numbers.
export const boot = reactive(window.inbox_boot || { numbers: [], tags: [] });

export function numberLabel(name) {
	return boot.numbers.find((n) => n.name === name)?.label || name || "";
}
