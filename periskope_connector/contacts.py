"""The unmatched-contact queue: every 1-to-1 number that is not in the CRM yet, in one working
list.

A Periskope Contact row is created on the first message from an unknown number (and kept up to
date afterwards). A manager resolves it from its form with one click: create a lead, link an
existing lead or deal, or ignore the number with a reason. Ignored numbers never come back.

Group chats are not queued here: they are managed on the Periskope Group (manual lead/deal
override), because a group has no single customer to add.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, escape_html, get_url, now_datetime

from periskope_connector import safety
from periskope_connector.linking import link_message, number_key, phone_e164
from periskope_connector.system import insert_as_system

IGNORED_STATUSES = ("Ignored (Personal)", "Ignored (Vendor)", "Ignored (Spam)", "Ignored (Internal)")


def _settings():
	return frappe.get_cached_doc("Periskope Settings")


def _recipients_text() -> list[str]:
	emails = _settings().alert_emails or ""
	return [e.strip() for e in re.split(r"[,\n;]", emails) if e.strip()]


# --- Ignore list -------------------------------------------------------------------------------------


def _erpnext_numbers() -> set[str]:
	"""Supplier and Employee phone numbers, when ERPNext is installed."""
	found: set[str] = set()
	if "erpnext" not in frappe.get_installed_apps():
		return found

	def digits_of(doctype: str, filters: dict, fields: list[str]) -> set[str]:
		out: set[str] = set()
		for field in fields:
			if not frappe.get_meta(doctype).has_field(field):
				continue
			values = frappe.get_all(
				doctype, filters={**filters, field: ["is", "set"]}, pluck=field, limit=5000
			)
			out |= {phone_e164(v) for v in values}
		return out

	if frappe.db.exists("DocType", "Supplier"):
		# A Supplier's phones live on its linked Frappe Contacts (Dynamic Link child table).
		supplier_contacts = [
			row[0]
			for row in frappe.db.sql(
				"select distinct parent from `tabDynamic Link` where link_doctype = 'Supplier'"
			)
		]
		if supplier_contacts:
			found |= digits_of("Contact", {"name": ["in", supplier_contacts]}, ["mobile_no", "phone"])
	if frappe.db.exists("DocType", "Employee"):
		found |= digits_of("Employee", {}, ["cell_number", "phone", "mobile_no"])
	return found


def is_ignored(full_number: str) -> bool:
	"""True for an ignored Periskope Contact (the ignore list: managers ignore a number from its form,
	the agent suggests it), or for an ERPNext Supplier or Employee number."""
	digits = phone_e164(full_number)
	if not digits:
		return True
	status = frappe.db.get_value("Periskope Contact", {"phone": digits}, "status")
	if status in IGNORED_STATUSES:
		return True
	if digits in _erpnext_numbers():
		return True
	return False


# --- The queue itself ---------------------------------------------------------------------------------


def _stats(digits: str) -> frappe._dict:
	"""Message totals and the latest AI category of one 1-to-1 number."""
	row = frappe.db.sql(
		"""select count(*) as count, min(message_timestamp) as first_at, max(message_timestamp) as last_at,
		substring_index(group_concat(nullif(ai_category, '') order by message_timestamp desc), ',', 1) as category,
		group_concat(distinct nullif(rep, '')) as reps
		from `tabPeriskope Message` where is_group = 0 and contact_number = %s""",
		(digits,),
		as_dict=True,
	)[0]
	return row


def ensure_contact(doc) -> str | None:
	"""Create or refresh the queue row of an unknown 1-to-1 number. Returns its name, or None when
	the number is not queueable (group, ignored, already linked). Never raises."""
	try:
		if doc.is_group or not doc.contact_number:
			return None
		digits = phone_e164(doc.contact_number)
		if not digits:
			return None
		if is_ignored(digits):
			return None
		stats = _stats(digits)
		existing = frappe.db.get_value(
			"Periskope Contact", {"phone": digits}, ["name", "status"], as_dict=True
		)
		if existing:
			frappe.db.set_value(
				"Periskope Contact",
				existing.name,
				{
					"display_name": doc.sender_name or "",
					"message_count": stats.count,
					"first_message_at": stats.first_at,
					"last_message_at": stats.last_at,
					"last_category": stats.category or "",
					"reps": stats.reps or "",
				},
				update_modified=False,
			)
			return existing.name
		return insert_as_system(
			{
				"doctype": "Periskope Contact",
				"phone": digits,
				"phone_key": number_key(digits),
				"display_name": doc.sender_name or "",
				"status": "New",
				"message_count": stats.count,
				"first_message_at": stats.first_at,
				"last_message_at": stats.last_at,
				"last_category": stats.category or "",
				"reps": stats.reps or "",
			}
		).name
	except Exception:
		frappe.log_error(title=f"Periskope: could not update the contact queue for {doc.contact_number}")
		return None


# --- One-click actions --------------------------------------------------------------------------------


def _phone_fields(values: dict) -> dict:
	values = dict(values)
	digits = phone_e164(values.get("mobile_no") or values.get("phone"))
	values.update(
		{
			"periskope_phone_e164": digits,
			"periskope_phone_key": number_key(digits) if digits else "",
		}
	)
	return values


def backfill_and_comment(contact):
	"""After a link or create: re-link every past message of the number and post one summary
	comment on the record."""
	linked = frappe.get_all(
		"Periskope Message",
		filters={"contact_number": phone_e164(contact.phone), "is_group": 0, "link_status": ["!=", "Linked"]},
		pluck="name",
		limit=5000,
	)
	for name in linked:
		link_message(name)

	target = ("CRM Deal", contact.linked_deal) if contact.linked_deal else ("CRM Lead", contact.linked_lead)
	if target and contact.message_count:
		first = contact.first_message_at.strftime("%d %b %Y") if contact.first_message_at else ""
		last = contact.last_message_at.strftime("%d %b %Y") if contact.last_message_at else ""
		preview = frappe.db.get_value(
			"Periskope Message",
			{"contact_number": phone_e164(contact.phone), "is_group": 0, "body": ["is", "set"]},
			"body",
			order_by="message_timestamp desc",
		)
		insert_as_system(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": target[0],
				"reference_name": target[1],
				"comment_email": "Administrator",
				"owner": "Administrator",
				"content": (
					f"<p><b>WhatsApp history linked</b>: {contact.message_count} message(s) with "
					f"{escape_html(contact.display_name or contact.phone)} "
					f"({first} &ndash; {last}).</p>"
					f"<p>Latest: {escape_html((preview or '')[:300])}</p>"
				),
			}
		)


CONTACT_FIELDS = [
	"status",
	"phone",
	"display_name",
	"reps",
	"linked_lead",
	"linked_deal",
	"message_count",
	"first_message_at",
	"last_message_at",
]


def _contact(contact: str) -> frappe._dict:
	"""The fields of a queue row that the actions read. Nothing here changes the row."""
	values = frappe.db.get_value("Periskope Contact", contact, CONTACT_FIELDS, as_dict=True)
	if not values:
		frappe.throw(_("Contact {0} does not exist").format(contact), frappe.DoesNotExistError)
	return values


def create_lead_for_contact(contact: str) -> str | None:
	"""Queue action: create a CRM Lead from the contact. Returns the lead's name."""
	from periskope_connector.leads import ensure_lead_source

	doc = _contact(contact)
	if doc.status in IGNORED_STATUSES:
		frappe.throw(_("This number is ignored. Remove the ignore to create a lead."))
	if doc.linked_lead or doc.linked_deal:
		frappe.throw(_("This contact already has a lead or deal."))

	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": doc.display_name or doc.phone,
			"mobile_no": f"+{doc.phone}",
			"source": ensure_lead_source(),
			"lead_owner": doc.reps.split(",")[0] if doc.reps else None,
		}
	)
	lead.insert(ignore_permissions=True)
	name = lead.name

	frappe.db.set_value("Periskope Contact", contact, {"status": "Lead Created", "linked_lead": name})
	backfill_and_comment(_contact(contact))
	return name


def link_contact(contact: str, lead: str | None = None, deal: str | None = None) -> None:
	"""Queue action: link the contact's history to an existing lead or deal."""
	doc = _contact(contact)
	if doc.status in IGNORED_STATUSES:
		frappe.throw(_("This number is ignored. Remove the ignore to link it."))
	if doc.linked_lead or doc.linked_deal:
		frappe.throw(_("This contact already has a lead or deal."))
	if not lead and not deal:
		frappe.throw(_("Pass a lead or a deal to link to."))
	if lead and not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead {0} does not exist").format(lead))
	if deal and not frappe.db.exists("CRM Deal", deal):
		frappe.throw(_("Deal {0} does not exist").format(deal))

	if lead:
		values = frappe.db.get_value("CRM Lead", lead, ["mobile_no", "phone"], as_dict=True)
		frappe.db.set_value("CRM Lead", lead, _phone_fields({**values, "mobile_no": f"+{doc.phone}"}))
	if deal:
		values = frappe.db.get_value("CRM Deal", deal, ["mobile_no", "phone"], as_dict=True)
		frappe.db.set_value("CRM Deal", deal, _phone_fields({**values, "mobile_no": f"+{doc.phone}"}))

	frappe.db.set_value(
		"Periskope Contact",
		contact,
		{"status": "Linked", "linked_lead": lead, "linked_deal": deal},
	)
	backfill_and_comment(_contact(contact))


def ignore_contact(contact: str, reason: str) -> None:
	"""Queue action: stop queueing this number. Reason must be one of the ignored statuses."""
	if reason not in IGNORED_STATUSES:
		frappe.throw(_("Reason must be one of: {0}").format(", ".join(IGNORED_STATUSES)))
	doc = frappe.get_doc("Periskope Contact", contact)
	doc.status = reason
	doc.flags.ignore_permissions = True
	doc.save()


# --- Daily digest ---------------------------------------------------------------------------------------


def digest_rows() -> list[frappe._dict]:
	"""New and still-unresolved contacts with activity in the last 24 h, most active first."""
	return frappe.get_all(
		"Periskope Contact",
		filters={"status": "New", "last_message_at": [">=", add_days(now_datetime(), -1)]},
		fields=["phone", "display_name", "message_count", "last_category", "last_message_at"],
		order_by="message_count desc",
		limit=50,
	)


def send_digest() -> int:
	"""Scheduler, daily: yesterday's unknown numbers to the sales head."""
	emails = _recipients_text()
	rows = digest_rows()
	if not emails or not rows:
		return 0
	head = "".join(
		f"<th style='padding:4px 8px;border:1px solid #ddd;text-align:left'>{h}</th>"
		for h in (_("Number"), _("Name"), _("Messages"), _("Latest AI Category"), _("Last Message"))
	)
	body = "".join(
		"<tr>"
		+ "".join(
			f"<td style='padding:4px 8px;border:1px solid #ddd'>{escape_html(str(v or ''))}</td>"
			for v in (
				r.phone,
				r.display_name,
				r.message_count,
				(r.last_category or "").replace("_", " "),
				r.last_message_at,
			)
		)
		+ "</tr>"
		for r in rows
	)
	safety.sendmail(
		recipients=emails,
		subject=_("WhatsApp numbers waiting to be added to the CRM ({0})").format(len(rows)),
		message=(
			f"<p>{_('These customers wrote to a company WhatsApp number in the last 24 hours but are not on any lead or deal yet.')}</p>"
			f"<table style='border-collapse:collapse;font-size:13px'><tr>{head}</tr>{body}</table>"
			f"<p><a href='{get_url()}/app/periskope-contact'>{_('Open the contact queue')}</a></p>"
		),
	)
	return len(rows)


# --- One-off back-fill (patch) -----------------------------------------------------------------------------


def backfill_contacts() -> int:
	"""Create queue rows for the unknown numbers already in the message history.
	Run with: bench --site <site> execute periskope_connector.contacts.backfill_contacts
	"""
	digits_list = frappe.get_all(
		"Periskope Message",
		filters={"is_group": 0, "link_status": "No Match", "contact_number": ["is", "set"]},
		pluck="contact_number",
		distinct=True,
	)
	created = 0
	for digits in digits_list:
		if frappe.db.exists("Periskope Contact", {"phone": phone_e164(digits)}):
			continue
		stats = _stats(phone_e164(digits))
		insert_as_system(
			{
				"doctype": "Periskope Contact",
				"phone": phone_e164(digits),
				"phone_key": number_key(phone_e164(digits)),
				"display_name": frappe.db.get_value(
					"Periskope Message",
					{"contact_number": phone_e164(digits), "is_group": 0, "sender_name": ["is", "set"]},
					"sender_name",
					order_by="message_timestamp desc",
				)
				or "",
				"status": "New",
				"message_count": stats.count,
				"first_message_at": stats.first_at,
				"last_message_at": stats.last_at,
				"last_category": stats.category or "",
				"reps": stats.reps or "",
			}
		)
		created += 1
	return created
