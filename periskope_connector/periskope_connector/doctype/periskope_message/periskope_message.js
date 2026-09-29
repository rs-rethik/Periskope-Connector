frappe.ui.form.on("Periskope Message", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user_roles.includes("System Manager")) return;
		frm.add_custom_button(__("Tag Again with AI"), () => {
			frappe.call({
				method: "periskope_connector.ai.classifier.reclassify",
				args: { message: frm.doc.name },
				freeze: true,
				callback() {
					frappe.show_alert({
						message: __("Queued. Refresh in a few seconds to see the new tags."),
						indicator: "green",
					});
					frm.reload_doc();
				},
			});
		});
	},
});
