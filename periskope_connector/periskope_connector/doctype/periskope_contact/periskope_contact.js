frappe.ui.form.on("Periskope Contact", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.linked_lead || frm.doc.linked_deal) return;
		const ignored = (frm.doc.status || "").startsWith("Ignored");

		if (!ignored) {
			frm.add_custom_button(__("Create Lead"), () => create_lead(frm), __("Actions"));
			frm.add_custom_button(
				__("Link to Lead or Deal"),
				() => link_dialog(frm),
				__("Actions")
			);
			frm.add_custom_button(__("Ignore"), () => ignore_dialog(frm), __("Actions"));
		} else {
			frm.add_custom_button(
				__("Un-ignore (back to New)"),
				() => {
					frm.set_value("status", "New");
					frm.save();
				},
				__("Actions")
			);
		}
		frm.add_custom_button(__("Show Messages"), () => {
			frappe.set_route("List", "Periskope Message", {
				contact_number: frm.doc.phone,
			});
		});
	},
});

function create_lead(frm) {
	frappe.call({
		method: "periskope_connector.api.contacts.create_lead",
		args: { contact: frm.doc.name },
		freeze: true,
		freeze_message: __("Creating the lead and linking its history..."),
		callback(r) {
			frappe.show_alert({
				message: __("Lead {0} created", [r.message]),
				indicator: "green",
			});
			frappe.set_route("Form", "CRM Lead", r.message);
		},
	});
}

function link_dialog(frm) {
	frappe.call({
		method: "periskope_connector.api.contacts.find_matches",
		args: { contact: frm.doc.name },
		callback(r) {
			const matches = r.message || [];
			const dialog = new frappe.ui.Dialog({
				title: __("Link {0} to the CRM", [frm.doc.display_name || frm.doc.phone]),
				fields: [
					{
						fieldname: "match",
						fieldtype: "Select",
						label: __("CRM Record"),
						options: [
							{ label: `— ${__("choose")} —`, value: "" },
							...matches.map((m) => ({
								label: `${m.label}: ${m.title} (${m.name})`,
								value: `${m.doctype}|${m.name}`,
							})),
						],
					},
					{
						fieldname: "lead",
						fieldtype: "Link",
						label: __("...or pick a Lead by hand"),
						options: "CRM Lead",
					},
					{
						fieldname: "deal",
						fieldtype: "Link",
						label: __("...or pick a Deal by hand"),
						options: "CRM Deal",
					},
				],
				primary_action_label: __("Link"),
				primary_action(values) {
					let lead = values.lead || "";
					let deal = values.deal || "";
					if (values.match) {
						const [doctype, name] = values.match.split("|");
						if (doctype === "CRM Lead") lead = name;
						else deal = name;
					}
					if (!lead && !deal) {
						frappe.msgprint(__("Choose or pick a record first."));
						return;
					}
					dialog.hide();
					frappe.call({
						method: "periskope_connector.api.contacts.link_to",
						args: { contact: frm.doc.name, lead, deal },
						freeze: true,
						freeze_message: __("Linking the message history..."),
						callback() {
							frappe.show_alert({
								message: __("Linked, and the history is on the record now."),
								indicator: "green",
							});
							frm.reload_doc();
						},
					});
				},
			});
			dialog.show();
		},
	});
}

function ignore_dialog(frm) {
	const reasons = [
		"Ignored (Personal)",
		"Ignored (Vendor)",
		"Ignored (Spam)",
		"Ignored (Internal)",
	];
	const dialog = new frappe.ui.Dialog({
		title: __("Ignore this number"),
		fields: [
			{
				fieldname: "reason",
				fieldtype: "Select",
				label: __("Reason"),
				options: reasons,
				default: reasons[0],
			},
		],
		primary_action_label: __("Ignore"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "periskope_connector.api.contacts.ignore",
				args: { contact: frm.doc.name, reason: values.reason },
				callback() {
					frappe.show_alert({ message: __("Ignored."), indicator: "orange" });
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}
