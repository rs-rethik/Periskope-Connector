import { dialog } from "frappe-ui";

// The app's yes/no question, as a frappe-ui dialog (never the browser's confirm box). Resolves true
// when the user confirms; Cancel, Esc and a click outside resolve false.
export function ask(
	message,
	{ title = "Are you sure?", confirmLabel = "Continue", danger = false } = {}
) {
	return new Promise((resolve) => {
		dialog.confirm({
			title,
			message,
			confirmLabel,
			theme: danger ? "red" : undefined,
			onConfirm: () => resolve(true),
			onCancel: () => resolve(false),
		});
	});
}

export const discardChanges = () =>
	ask("Your changes are not saved yet. Leave them?", {
		title: "Discard changes?",
		confirmLabel: "Discard",
		danger: true,
	});
