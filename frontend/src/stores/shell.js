import { reactive } from "vue";

// What App.vue keeps mounted outside the sidebar, so it stays open when the phone's menu drawer
// closes: Settings (loaded when first opened) and, on a phone, the notifications sheet.
export const shell = reactive({ settings: false, settingsUsed: false, notifications: false });

export function openSettings() {
	shell.settingsUsed = true;
	shell.settings = true;
}
