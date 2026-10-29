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
		# the WhatsApp message a rep made this deal from (api/crm_panel.create_deal_from_message)
		dict(
			fieldname="periskope_message",
			label="From WhatsApp Message",
			fieldtype="Link",
			options="Periskope Message",
			insert_after="lead",
			read_only=1,
			no_copy=1,
		),
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


def after_install():
	setup()


def after_migrate():
	setup()


def _ensure_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)


def setup():
	"""Create the role, custom fields, lead source, starter records and default settings. Safe to
	re-run. Each step runs on its own: one that fails (say, settings an admin left invalid) is logged
	and the rest still run, so test mode and the defaults are never skipped because of it."""
	for step in (
		_ensure_role,
		_ensure_custom_fields,
		_ensure_lead_source,
		_ensure_tags,
		_ensure_working_hours,
		_ensure_webhook_events,
		_ensure_assistant_defaults,
		_ensure_crm_ui,
		_ensure_phone_fields,
	):
		try:
			step()
		except Exception:
			# A half-installed site must still be able to migrate; the gap shows in Health.
			frappe.log_error(title=f"Periskope: install step {step.__name__} failed")


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
	"""Single doctypes do not apply field defaults until saved, so store the starting values once.
	Auto reply starts off: the AI does everything else on its own, and answering customers is the
	one thing the team switches on."""
	if not _stored("reply_delay_minutes"):
		frappe.db.set_single_value("Periskope Settings", "reply_delay_minutes", 15)
	# New numbers are welcomed once "Reply to Customers" is on (welcome.py).
	if not _stored("welcome_new_numbers"):
		frappe.db.set_single_value("Periskope Settings", "welcome_new_numbers", 1)
	# Test mode starts on, with only the owner's own number and the approvers allowed. It is seeded
	# once: an admin who switches it off later is not overridden by the next migrate.
	if not _stored("test_mode"):
		frappe.db.set_single_value(
			"Periskope Settings",
			{
				"test_mode": 1,
				"test_allowed_chats": "916374955805",
				"test_allowed_emails": "rethik@praneraservices.com\npriyadharshini@agamnext.com",
			},
		)


def _ensure_phone_fields():
	"""Fill the normalised phone fields of the leads and deals that existed before the app (saves keep
	them up to date afterwards), then link the messages that found no lead because of it."""
	from periskope_connector import linking

	filled = sum(linking.backfill_phone_fields(doctype) for doctype in ("CRM Lead", "CRM Deal"))
	if filled:
		linking.link_unlinked()


def _stored(field: str) -> bool:
	"""Whether Periskope Settings has a value stored for `field`, 0 included. (frappe.db.exists cannot
	tell: Singles is a table, not a doctype, so it always answered no and every migrate re-seeded.)"""
	singles = frappe.qb.DocType("Singles")
	return bool(
		frappe.qb.from_(singles)
		.select(singles.field)
		.where((singles.doctype == "Periskope Settings") & (singles.field == field))
		.run()
	)


def _ensure_tags():
	"""Create the starting tags on a site that has none. After that the team owns the list."""
	from periskope_connector.tags import DASHBOARD_GROUPS, DEFAULT_TAGS, FALLBACK_DEFAULT

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
			}
		).insert(ignore_permissions=True)


def _ensure_working_hours():
	"""A Time field's default is not applied to a new single doctype: the form fills in the current
	time instead, which made the working window zero-length. Store the usual hours up front."""
	for field, value in (("working_hours_start", "09:00:00"), ("working_hours_end", "19:00:00")):
		if not frappe.db.get_single_value("Periskope Settings", field):
			frappe.db.set_single_value("Periskope Settings", field, value)


# On a CRM lead or deal: open its WhatsApp chat in the inbox (the newest, on whichever number).
INBOX_SCRIPT = """class %(cls)s {
	onLoad() {
		call("periskope_connector.api.crm_panel.chats_for_record", { doctype: "%(dt)s", name: this.doc.name }).then((chats) => {
			if (!chats || !chats.length || this.actions.find((a) => a.periskopeInbox)) return
			this.actions.push({
				periskopeInbox: true,
				label: __("Open WhatsApp inbox"),
				onClick: () => window.open("/whatsapp/inbox/" + encodeURIComponent(chats[0].name), "_blank"),
			})
		})
	}
}
"""


def _ensure_crm_ui():
	"""The Periskope WhatsApp account, so the CRM shows its WhatsApp tab, and the "Open WhatsApp
	inbox" action on leads and deals."""
	from periskope_connector.mirror import ensure_account

	ensure_account()
	if not frappe.db.exists("DocType", "CRM Form Script"):
		return
	for dt in ("CRM Lead", "CRM Deal"):
		name = f"Periskope Inbox ({dt})"
		script = INBOX_SCRIPT % {"cls": dt.replace(" ", ""), "dt": dt}
		if frappe.db.exists("CRM Form Script", name):
			frappe.db.set_value("CRM Form Script", name, {"script": script, "enabled": 1})
			continue
		doc = frappe.get_doc(
			{"doctype": "CRM Form Script", "dt": dt, "view": "Form", "enabled": 1, "script": script}
		)
		doc.insert(set_name=name, ignore_permissions=True)
