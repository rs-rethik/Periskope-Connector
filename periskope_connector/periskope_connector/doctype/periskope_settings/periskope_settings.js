frappe.ui.form.on("Periskope Settings", {
	onload(frm) {
		// Offer Jarvis only when the Jarvis app is installed on this site.
		const providers = (frm.doc.__onload && frm.doc.__onload.ai_providers) || ["Direct API"];
		frm.set_df_property("ai_provider", "options", providers.join("\n"));
		if (!providers.includes(frm.doc.ai_provider)) frm.set_value("ai_provider", providers[0]);
	},

	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => test_connection(frm), __("Actions"));
		frm.add_custom_button(__("Sync Phones"), () => sync_phones(frm), __("Actions"));
		frm.add_custom_button(
			__("Create Webhook in Periskope"),
			() => setup_webhooks(frm),
			__("Actions")
		);
		frm.add_custom_button(__("Test AI Connection"), () => test_ai(frm), __("Actions"));
		frm.add_custom_button(
			__("Copy Webhook URL"),
			() => frappe.utils.copy_to_clipboard(frm.doc.webhook_url),
			__("Actions")
		);

		refresh_health(frm);
		render_setup(frm);
	},

	// Buttons inside the setup steps.
	test_periskope(frm) {
		test_connection(frm);
	},
	test_ai(frm) {
		test_ai(frm);
	},
});

// Save first so the key/URL typed in the form is what gets tested.
function with_saved(frm, fn) {
	if (frm.is_dirty()) {
		frm.save().then(fn);
	} else {
		fn();
	}
}

function test_connection(frm) {
	with_saved(frm, () => {
		frappe.call({
			method: "periskope_connector.api.settings.test_connection",
			freeze: true,
			freeze_message: __("Connecting to Periskope..."),
			callback(r) {
				if (!r.message) return;
				frappe.show_alert({
					message: __("Connected: {0} phone(s)", [r.message.phone_count]),
					indicator: "green",
				});
				if (r.message.webhooks) show_webhooks(r.message.webhooks);
				if (r.message.webhooks_error) {
					frappe.msgprint({
						title: __("Webhook not created"),
						message: r.message.webhooks_error,
						indicator: "orange",
					});
				}
				frm.reload_doc();
			},
			error() {
				// Server stored the failure in connection_status.
				frm.reload_doc();
			},
		});
	});
}

function setup_webhooks(frm) {
	with_saved(frm, () => {
		frappe.call({
			method: "periskope_connector.api.settings.setup_webhooks",
			freeze: true,
			freeze_message: __("Creating the webhook in Periskope..."),
			callback(r) {
				if (r.message) show_webhooks(r.message);
			},
		});
	});
}

function show_webhooks(result) {
	const lines = [
		result.created.length
			? __("Created for: {0}", [result.created.join(", ")])
			: __("Nothing to create: the webhook already exists."),
		__(
			"Periskope does not give the signing secret through the API. Copy it from Periskope (Settings > Webhooks) into Webhook Signing Secret, then save."
		),
	];
	if (result.stale.length) {
		lines.push(
			__("Older webhooks for this app still point to: {0}. Remove them in Periskope.", [
				result.stale.join(", "),
			])
		);
	}
	frappe.msgprint({
		title: __("Periskope webhook"),
		message: lines.map((l) => `<p>${frappe.utils.escape_html(l)}</p>`).join(""),
		indicator: "green",
	});
}

// The Health tab's read-only fields are filled live, so they can never go stale.
function refresh_health(frm) {
	if (!frm.fields_dict.health_last_webhook) return;
	frappe.call({
		method: "periskope_connector.api.settings.get_health",
		callback(r) {
			const h = r.message || {};
			// Shown, not edited: assigning directly keeps the form from being marked unsaved.
			const values = {
				health_last_webhook: `${h.last_webhook || "-"}`,
				health_last_ai_run: `${h.last_ai_run || "-"}`,
				health_pending: `${h.pending ?? 0}`,
				health_failed: `${h.failed ?? 0}`,
				health_unprocessed: `${h.unprocessed ?? 0}`,
				health_month_tokens: `${h.month_tokens ?? 0}`,
				health_phones: h.phones || "-",
				health_agent: h.agent || "-",
				health_auto_reply: h.auto_reply || "-",
				health_crm: h.crm || "-",
				health_budget: h.budget || "-",
				health_test_mode: h.test_mode || "-",
				health_last_problem: h.last_problem || "-",
			};
			Object.entries(values).forEach(([field, value]) => {
				frm.doc[field] = value;
				frm.refresh_field(field);
			});
		},
	});
}

function test_ai(frm) {
	with_saved(frm, () => {
		frappe.call({
			method: "periskope_connector.api.settings.test_ai_connection",
			freeze: true,
			freeze_message: __("Contacting the AI..."),
			callback(r) {
				if (!r.message) return;
				frappe.show_alert({
					message: __("AI connected ({0})", [r.message.model]),
					indicator: "green",
				});
				frm.reload_doc();
			},
			error() {
				// The server stored the failure in AI Status.
				frm.reload_doc();
			},
		});
	});
}

function sync_phones(frm) {
	with_saved(frm, () => {
		frappe.call({
			method: "periskope_connector.api.settings.sync_phones",
			freeze: true,
			freeze_message: __("Syncing phones..."),
			callback(r) {
				// The numbers are WhatsApp Number records; the rep of each is set there.
				frappe.show_alert({
					message: __("Synced {0} WhatsApp number(s)", [(r.message || []).length]),
					indicator: "green",
				});
				frm.reload_doc();
			},
		});
	});
}

// The four setup steps at the top of the Setup tab, and a red banner while test mode is on.
function render_setup(frm) {
	const field = frm.fields_dict.setup_status;
	if (!field) return;
	frappe.call({
		method: "periskope_connector.api.settings.get_setup_status",
		callback(r) {
			const status = r.message;
			if (!status) return;
			const e = frappe.utils.escape_html;
			const rows = status.steps
				.map(
					(step, i) =>
						`<div style="display:flex;gap:10px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--border-color)">
							<span class="indicator-pill ${step.ok ? "green" : "orange"}">${
							step.ok ? __("Done") : __("To do")
						}</span>
							<b>${i + 1}. ${e(step.title)}</b>
							<span class="text-muted">${e(step.message || "")}</span>
						</div>`
				)
				.join("");
			const headline = status.ready
				? __(
						"The AI agent is running: it reads every message, image and document, creates tasks, escalates and keeps the CRM up to date."
				  )
				: __("Finish steps 1 to 3; the AI agent starts on its own when they are done.");
			const warnings = (status.warnings || [])
				.map((w) => `<p class="text-danger" style="margin:8px 0 0">${e(w)}</p>`)
				.join("");
			field.$wrapper.html(`<p>${e(headline)}</p>${rows}${warnings}`);
			frm.dashboard.clear_headline();
			if (status.test_mode) {
				frm.dashboard.set_headline_alert(
					__(
						"Test mode is on: WhatsApp messages go only to the allowed chats, emails only to the allowed addresses. Everything else is a dry run (see Periskope Agent Action)."
					),
					"red"
				);
			}
		},
	});
}
