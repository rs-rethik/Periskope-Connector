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


# ERPNext's doctypes get fields only on a site that has ERPNext.
ERPNEXT_CUSTOM_FIELDS = {
	"Sales Order": [
		# the WhatsApp message a rep made this order from (api/sales_order.py)
		dict(
			fieldname="periskope_message",
			label="From WhatsApp Message",
			fieldtype="Link",
			options="Periskope Message",
			insert_after="customer",
			read_only=1,
			no_copy=1,
			print_hide=1,
			search_index=1,
		),
	],
}


def _erpnext_fields() -> dict:
	return {dt: fields for dt, fields in ERPNEXT_CUSTOM_FIELDS.items() if frappe.db.exists("DocType", dt)}


def _ensure_custom_fields():
	create_custom_fields({**CUSTOM_FIELDS, **_erpnext_fields()}, ignore_validate=True, update=True)


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
		_ensure_crm_indexes,
	):
		try:
			step()
		except Exception:
			# A half-installed site must still be able to migrate; the gap shows in Health.
			frappe.log_error(title=f"Periskope: install step {step.__name__} failed")


# The CRM columns this app filters on for every message and inbox load (linking.py, access.py,
# api/crm_panel.py); the CRM indexes none of them.
CRM_INDEXES = {
	"CRM Lead": ["lead_owner", "organization"],
	"CRM Deal": ["deal_owner", "lead", "organization", "periskope_message"],
}


def _ensure_crm_indexes():
	for doctype, fields in CRM_INDEXES.items():
		if not frappe.db.table_exists(doctype):
			continue
		columns = set(frappe.db.get_table_columns(doctype))
		for field in fields:
			if field in columns:
				frappe.db.add_index(doctype, [field], index_name=f"periskope_{field}")
	# the chat list's latest AI tag per chat (api/inbox.CHAT_COLUMNS): a chat's messages by time
	frappe.db.add_index(
		"Periskope Message", ["periskope_chat", "message_timestamp"], index_name="periskope_chat_time"
	)


def before_uninstall():
	"""Remove what the app added to other apps' doctypes: its custom fields, the CRM's "Open WhatsApp
	inbox" form scripts (their page goes with the app) and the WhatsApp Manager role when no other
	app uses it (frappe_whatsapp does). The app's own doctypes, and their rows, go with the app. The
	"WhatsApp" lead source stays: leads keep it as their source."""
	try:
		from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields

		# Frappe's own helper takes the same {doctype: [fields]} shape the install uses.
		delete_custom_fields({**CUSTOM_FIELDS, **_erpnext_fields()})
	except Exception:
		frappe.log_error(title="Periskope: before-uninstall cleanup failed")
	try:
		if frappe.db.exists("DocType", "CRM Form Script"):
			for dt in ("CRM Lead", "CRM Deal"):
				for name in (f"Periskope Inbox ({dt})", f"Periskope AI ({dt})"):
					if frappe.db.exists("CRM Form Script", name):
						frappe.delete_doc("CRM Form Script", name, ignore_permissions=True, force=True)
		if frappe.db.exists("Role", ROLE) and not _role_used_elsewhere():
			frappe.delete_doc("Role", ROLE, ignore_permissions=True, force=True)
	except Exception:
		frappe.log_error(title="Periskope: before-uninstall cleanup failed")


def _role_used_elsewhere() -> bool:
	"""Whether a doctype of another app gives the role any permission."""
	ours = set(frappe.get_all("DocType", filters={"module": "Periskope Connector"}, pluck="name"))
	used = set(frappe.get_all("DocPerm", filters={"role": ROLE}, pluck="parent"))
	used |= set(frappe.get_all("Custom DocPerm", filters={"role": ROLE}, pluck="parent"))
	return bool(used - ours)


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
	# Test mode starts on with empty allow lists: no WhatsApp message and no email of this app leaves
	# the site until an admin lists who may get them (or switches test mode off). Other apps' emails
	# are not held unless the admin asks for it (test_hold_all_emails). Seeded once: an admin who
	# switches it off later is not overridden by the next migrate.
	if not _stored("test_mode"):
		frappe.db.set_single_value("Periskope Settings", "test_mode", 1)


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
	"""A Time field's default is not applied to a new single doctype: Frappe stores the install time
	in both fields instead, a window of a split second, so the settings could not be saved and the
	reply reminders never ran. Store the usual hours when the window is missing or that short; a
	window an admin set is kept."""
	from frappe.utils import get_time

	from periskope_connector.periskope_connector.doctype.periskope_settings.periskope_settings import (
		MIN_WORKING_MINUTES,
	)

	start = frappe.db.get_single_value("Periskope Settings", "working_hours_start")
	end = frappe.db.get_single_value("Periskope Settings", "working_hours_end")
	if start and end:
		start, end = get_time(start), get_time(end)
		if (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute) >= MIN_WORKING_MINUTES:
			return
	frappe.db.set_single_value(
		"Periskope Settings", {"working_hours_start": "09:00:00", "working_hours_end": "19:00:00"}
	)


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
	"""The "Open WhatsApp inbox" action on leads and deals."""
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
