frappe.ui.form.on("Periskope Suggestion", {
	refresh(frm) {
		if (frm.doc.status !== "Pending") return;
		for (const [label, decision] of [
			[__("Approve"), "approve"],
			[__("Reject"), "reject"],
		]) {
			frm.add_custom_button(label, () => {
				frappe.confirm(
					decision === "approve"
						? __("Apply this change now?")
						: __("Reject this suggestion?"),
					() =>
						frappe.call({
							method: "periskope_connector.suggestions.decide",
							args: { name: frm.doc.name, decision },
							freeze: true,
							callback: () => frm.reload_doc(),
						})
				);
			});
		}
	},
});
