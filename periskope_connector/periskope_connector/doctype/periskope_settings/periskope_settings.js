frappe.ui.form.on("Periskope Settings", {
	onload(frm) {
		// Offer Jarvis only when the Jarvis app is installed on this site.
		const providers = (frm.doc.__onload && frm.doc.__onload.ai_providers) || ["Direct API"];
		frm.set_df_property("ai_provider", "options", providers.join("\n"));
		if (!providers.includes(frm.doc.ai_provider)) frm.set_value("ai_provider", providers[0]);
	},

	refresh(frm) {
		// Phones come from Periskope via Sync Phones; users only pick the sales rep.
		const grid = frm.get_field("phones").grid;
		grid.cannot_add_rows = true;
		grid.cannot_delete_rows = true;
		grid.refresh();
		grid.wrapper.find(".grid-add-row, .grid-remove-rows").hide();

		frm.add_custom_button(__("Test Connection"), () => test_connection(frm), __("Actions"));
		frm.add_custom_button(__("Sync Phones"), () => sync_phones(frm), __("Actions"));
		frm.add_custom_button(
			__("Create Webhook in Periskope"),
			() => setup_webhooks(frm),
			__("Actions")
		);
		frm.add_custom_button(__("Test AI Connection"), () => test_ai(frm), __("Actions"));
		frm.add_custom_button(
			__("Check Reply Reminders Now"),
			() => check_reminders(frm),
			__("Actions")
		);
		frm.add_custom_button(
			__("Reset AI Instructions"),
			() =>
				frappe.confirm(__("Replace the AI instructions with the original text?"), () =>
					frappe.call({
						method: "periskope_connector.api.settings.get_default_ai_instructions",
						callback(r) {
							frm.set_value("ai_instructions", r.message);
							frappe.show_alert({
								message: __("Reset. Save to keep it."),
								indicator: "green",
							});
						},
					})
				),
			__("Actions")
		);
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

frappe.ui.form.on("Periskope Settings", {
	// Button on the AI Assistant tab: one pass of the learning agent now, instead of waiting 15 minutes.
	run_agent_now(frm) {
		with_saved(frm, () => {
			frappe.call({
				method: "periskope_connector.agent.run_now",
				freeze: true,
				freeze_message: __("The agent is reading new conversations..."),
				callback(r) {
					const out = r.message || {};
					frappe.msgprint(
						out.skipped
							? __("Skipped: {0}", [out.skipped])
							: __(
									"Read {0} message(s), learned {1} thing(s), {2} suggestion(s) now waiting for approval.",
									[out.messages || 0, out.knowledge || 0, out.suggestions || 0]
							  )
					);
				},
			});
		});
	},

	// Button under "Instructions for the AI". Nothing changes until the user accepts the result.
	optimize_instructions(frm) {
		const ask = new frappe.ui.Dialog({
			title: __("Improve Instructions with AI"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${__(
						"Your configured AI rewrites the instructions to be clearer and more reliable. You review the result before anything is saved."
					)}</p>`,
				},
				{
					fieldname: "goal",
					fieldtype: "Small Text",
					label: __("What should be better? (optional)"),
				},
			],
			primary_action_label: __("Improve"),
			primary_action(values) {
				ask.hide();
				with_saved(frm, () => {
					frappe.call({
						method: "periskope_connector.api.settings.optimize_ai_instructions",
						args: {
							instructions: frm.doc.ai_instructions || "",
							goal: values.goal || "",
						},
						freeze: true,
						freeze_message: __("Improving the instructions..."),
						callback(r) {
							if (r.message) review_instructions(frm, r.message);
						},
					});
				});
			},
		});
		ask.show();
	},
});

function review_instructions(frm, improved) {
	const review = new frappe.ui.Dialog({
		title: __("Review the Improved Instructions"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"You can edit this text. Your current instructions stay as they are unless you click Use This Version, and you still need to Save."
				)}</p>`,
			},
			{
				fieldname: "improved",
				fieldtype: "Long Text",
				label: __("Improved Instructions"),
				default: improved,
			},
		],
		primary_action_label: __("Use This Version"),
		primary_action(values) {
			review.hide();
			frm.set_value("ai_instructions", values.improved);
			frappe.show_alert({
				message: __("Applied. Click Save to keep it."),
				indicator: "green",
			});
		},
		secondary_action_label: __("Keep My Current Text"),
		secondary_action() {
			review.hide();
		},
	});
	review.show();
}

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

function check_reminders(frm) {
	with_saved(frm, () => {
		frappe.call({
			method: "periskope_connector.api.settings.run_reminder_check",
			freeze: true,
			freeze_message: __("Checking chats..."),
			callback(r) {
				const s = r.message || {};
				if (s.skipped) {
					frappe.msgprint({
						title: __("Nothing checked"),
						message: __("Skipped: {0}", [s.skipped]),
						indicator: "orange",
					});
					return;
				}
				let message = __(
					"Reps notified: {0}<br>ToDos + managers emailed: {1}<br>MD emailed: {2}<br>Closed (rep replied): {3}",
					[s.notified, s.escalated, s.md_emailed, s.closed]
				);
				if (s.errors) {
					message +=
						"<br>" + __("Chats that failed: {0}. See the Error Log.", [s.errors]);
				}
				frappe.msgprint({
					title: __("Reply reminders checked"),
					message: message,
					indicator: s.errors ? "orange" : "green",
				});
			},
		});
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
				frappe.show_alert({
					message: __("Synced {0} phone(s)", [(r.message || []).length]),
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
			const headline = status.done
				? __("Setup is complete. The AI agent is running.")
				: status.ready
				? __("Setup is done. Switch on Enable AI Agent in step 4 to start.")
				: __(
						"Finish the steps below; the AI agent can be switched on once steps 1 to 3 are done."
				  );
			field.$wrapper.html(`<p>${e(headline)}</p>${rows}`);
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
