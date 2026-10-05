frappe.ui.form.on("Periskope Message", {
	refresh(frm) {
		if (
			!frm.is_new() &&
			["Sales Manager", "System Manager"].some((r) => frappe.user_roles.includes(r))
		) {
			frm.add_custom_button(__("Correct Category"), () => {
				frappe.prompt(
					{
						fieldname: "category",
						fieldtype: "Select",
						label: __("The category the AI should have given"),
						options: frm.fields_dict.ai_category.df.options,
						default: frm.doc.correct_category || frm.doc.ai_category,
						reqd: 1,
					},
					(values) =>
						frappe.call({
							method: "periskope_connector.api.settings.set_correct_category",
							args: { message: frm.doc.name, category: values.category },
							callback: () => frm.reload_doc(),
						}),
					__("Correct Category")
				);
			});
		}
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
