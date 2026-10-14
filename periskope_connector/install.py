"""Install-time setup, so the app is plug-and-play: no manual DocType creation, roles or records.

Everything here is idempotent and also runs from `after_migrate`, so an upgrade (or a skipped
step) cannot leave a site half set up.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLE = "WhatsApp Manager"

# The normalised, indexed phone fields used for message matching (see linking.py). Hidden and
# read-only: sales keeps using the CRM's own mobile_no; the app maintains these underneath.
CUSTOM_FIELDS = {
	"CRM Lead": [
		dict(
			fieldname="periskope_phone_e164",
			label="Periskope Phone (E.164)",
			fieldtype="Data",
			insert_after="mobile_no",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
		dict(
			fieldname="periskope_phone_key",
			label="Periskope Phone Key (last 10)",
			fieldtype="Data",
			insert_after="periskope_phone_e164",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
	],
	"CRM Deal": [
		dict(
			fieldname="periskope_phone_e164",
			label="Periskope Phone (E.164)",
			fieldtype="Data",
			insert_after="mobile_no",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
		dict(
			fieldname="periskope_phone_key",
			label="Periskope Phone Key (last 10)",
			fieldtype="Data",
			insert_after="periskope_phone_e164",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
	],
}

# Starter keyword rules. Recipients are left empty on purpose: a rule with no recipients is
# listed but never sends, so nobody is emailed until an admin fills them in.
DEFAULT_KEYWORD_RULES = [
	(
		"Quality & Rejection",
		"rejected, rejection, poor quality, quality problem, quality issue, defective, damaged, "
		"shade variation, colour bleeding, color bleeding, reject the lot, redelivery, "
		"kalappu, kola vechutu, saapadu, saapu poiduchu",
	),
	(
		"Payment & PO",
		"po, p.o., purchase order, payment pending, amount pending, overdue, advance pending, "
		"cheque bounced, balance kedaikala, kudukala",
	),
	(
		"Legal & Serious",
		"legal, lawyer, legal notice, court, cheat, fraud, police, complaint, consumer court",
	),
]


def after_install():
	setup()


def after_migrate():
	setup()


def setup():
	"""Create the role, custom fields, lead source, starter keyword rules. Safe to re-run."""
	try:
		_ensure_role()
		create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
		_ensure_lead_source()
		_ensure_tags()
		_ensure_sla_levels()
		_ensure_ai_instructions()
		_ensure_working_hours()
		_ensure_keyword_rules()
		_ensure_webhook_events()
		_ensure_assistant_defaults()
	except Exception:
		# A half-installed site must still be able to migrate; the gap shows in Health.
		frappe.log_error(title="Periskope: install-time setup failed")


def before_uninstall():
	"""Remove what the app added to other doctypes. Data doctypes are left for the admin."""
	try:
		from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields

		# Frappe's own helper takes the same {doctype: [fields]} shape the install uses.
		delete_custom_fields(CUSTOM_FIELDS)
	except Exception:
		frappe.log_error(title="Periskope: before-uninstall cleanup failed")


def _ensure_role():
	if frappe.db.exists("Role", ROLE):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": ROLE,
			"desk_access": 1,
			"disabled": 0,
		}
	).insert(ignore_permissions=True)


def _ensure_lead_source():
	if not frappe.db.exists("DocType", "CRM Lead Source"):
		return
	from periskope_connector.leads import ensure_lead_source

	ensure_lead_source()


def _ensure_keyword_rules():
	"""Add the starter rules only when the settings table has never had any row."""
	settings = frappe.get_doc("Periskope Settings")
	if settings.keyword_rules:
		return
	for name, words in DEFAULT_KEYWORD_RULES:
		settings.append("keyword_rules", {"rule_name": name, "keywords": words, "enabled": 1})
	settings.flags.ignore_permissions = True
	settings.save()


def _ensure_webhook_events():
	"""Pre-select the usual events on a new site; the admin can change the selection afterwards."""
	from periskope_connector.webhooks import DEFAULT_EVENTS

	settings = frappe.get_doc("Periskope Settings")
	if settings.webhook_events:
		return
	for event in DEFAULT_EVENTS:
		settings.append("webhook_events", {"event": event})
	settings.flags.ignore_permissions = True
	settings.save()


def _ensure_assistant_defaults():
	"""Single doctypes do not apply field defaults until saved, so store the starting values of the
	AI Assistant tab once. The agent and the auto reply both start switched off."""
	defaults = {
		"learning_lookback_days": 14,
		"learning_min_evidence": 3,
		"suggestion_expiry_days": 7,
		"suggestion_email_gap_hours": 6,
		"reply_delay_minutes": 3,
		"reply_delay_jitter_percent": 20,
		"reply_min_confidence": 80,
		"reply_working_hours_only": 1,
		"reply_max_per_chat_per_day": 3,
		"holding_message": "Thanks for reaching out! We've noted your request, and our sales representative will contact you shortly.",
	}
	for field, value in defaults.items():
		if frappe.db.get_single_value("Periskope Settings", field) in (None, "", 0, "0"):
			frappe.db.set_single_value("Periskope Settings", field, value)


def _ensure_tags():
	"""Create the starting tags on a site that has none. After that the team owns the list."""
	from periskope_connector.tags import DASHBOARD_GROUPS, DEFAULT_TAGS, FALLBACK_DEFAULT, default_actions

	if frappe.db.count("Periskope Tag"):
		return
	for order, (name, description) in enumerate(DEFAULT_TAGS, start=1):
		frappe.get_doc(
			{
				"doctype": "Periskope Tag",
				"tag_name": name,
				"description": description,
				"active": 1,
				"is_default": 1 if name == FALLBACK_DEFAULT else 0,
				"sort_order": order * 10,
				"dashboard_group": DASHBOARD_GROUPS.get(name, ""),
				"actions": default_actions(name),
			}
		).insert(ignore_permissions=True)


def _ensure_sla_levels():
	"""Start a new site with the usual SLA ladder; the team edits it afterwards."""
	from periskope_connector.reminders import default_levels

	settings = frappe.get_doc("Periskope Settings")
	if settings.sla_levels:
		return
	for row in default_levels():
		settings.append(
			"sla_levels",
			{
				"wait_minutes": row.wait_minutes,
				"min_severity": row.min_severity,
				"action": row.action,
				"recipients": row.recipients,
			},
		)
	settings.flags.ignore_permissions = True
	settings.save()


def _ensure_ai_instructions():
	"""Show the default AI instructions in the settings so they can be edited."""
	from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS

	if (frappe.db.get_single_value("Periskope Settings", "ai_instructions") or "").strip():
		return
	frappe.db.set_single_value("Periskope Settings", "ai_instructions", DEFAULT_INSTRUCTIONS)


def _ensure_working_hours():
	"""A Time field's default is not applied to a new single doctype: the form fills in the current
	time instead, which made the working window zero-length. Store the usual hours up front."""
	for field, value in (("working_hours_start", "09:00:00"), ("working_hours_end", "19:00:00")):
		if not frappe.db.get_single_value("Periskope Settings", field):
			frappe.db.set_single_value("Periskope Settings", field, value)
