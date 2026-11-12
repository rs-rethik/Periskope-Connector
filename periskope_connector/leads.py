"""Create a CRM Lead from a new enquiry on WhatsApp.

The AI decides that a message is a new business enquiry (classifier: create_lead). The lead is
then made only when all of these hold too, so it is never made by mistake:
- it is a customer message in a 1-to-1 chat (a group has no single customer),
- the number is not already on any CRM lead or deal, nor on a Frappe Contact,
- the number is not one the team ignored (personal, vendor, spam, a colleague).

The lead gets the name the customer gave (found by the AI, else their phone number), the rep of the
phone as its owner and WhatsApp as its source, and the earlier
messages of that chat are linked to it.
"""

import frappe
from redis.exceptions import LockError

from periskope_connector import safety
from periskope_connector.contacts import is_ignored
from periskope_connector.linking import by_mobile, link_message, number_key, phone_e164

DEFAULT_SOURCE = "WhatsApp"
LOCK_SECONDS = 120  # a lock nobody released (a killed worker) frees itself after this
LOCK_WAIT_SECONDS = 20

# The message fields that decide a lead and fill it in.
MESSAGE_FIELDS = [
	"direction",
	"is_group",
	"chat_id",
	"contact_number",
	"contact_name",
	"ai_category",
	"severity",
	"rep",
]


def _known_contact(digits: str) -> bool:
	"""True when the number is on a Frappe Contact already (e.g. an ERPNext customer contact):
	a second lead for the same person would be a duplicate. Exact matches only, so no scans.
	The Contact controller keeps phones in its Contact Phone child table, so check both places."""
	if not digits:
		return False
	return bool(
		frappe.db.exists("Contact Phone", {"parenttype": "Contact", "phone": f"+{digits}"})
		or frappe.db.exists("Contact Phone", {"parenttype": "Contact", "phone": digits})
		or frappe.db.exists("Contact", {"mobile_no": f"+{digits}"})
		or frappe.db.exists("Contact", {"mobile_no": digits})
	)


def ensure_lead_source() -> str | None:
	"""The WhatsApp lead source, created when the CRM does not have it yet."""
	if not frappe.db.exists("DocType", "CRM Lead Source"):
		return None
	if not frappe.db.exists("CRM Lead Source", DEFAULT_SOURCE):
		# The app acts here, not a user: the job that creates a lead may run as Guest.
		frappe.get_doc({"doctype": "CRM Lead Source", "source_name": DEFAULT_SOURCE}).insert(
			ignore_permissions=True
		)
	return DEFAULT_SOURCE


def is_new_enquiry(doc) -> bool:
	"""A customer's 1-to-1 message with a number. That it is a business enquiry is the AI's call."""
	return doc.direction == "Incoming" and not doc.is_group and bool(number_key(doc.contact_number))


def _owner(doc) -> str | None:
	if doc.rep and frappe.db.get_value("User", doc.rep, "enabled"):
		return doc.rep
	return None


def contact_name(doc) -> str:
	"""The name the customer gave in this message, or else in an earlier message of the chat."""
	if doc.contact_name:
		return doc.contact_name.strip()
	given = frappe.get_all(
		"Periskope Message",
		filters={"contact_number": doc.contact_number, "is_group": 0, "contact_name": ["is", "set"]},
		pluck="contact_name",
		order_by="message_timestamp desc",
		limit=1,
	)
	return (given[0] if given else "").strip()


def split_name(full: str, fallback: str) -> tuple[str, str]:
	"""(first name, last name). CRM needs a first name, so the phone number stands in when the
	customer has not given one."""
	first, _sep, last = " ".join(full.split()).partition(" ")
	return (first or fallback, last)


def create_lead(doc) -> str | None:
	"""Make the lead for this message's number. Returns its name, or None when not allowed (or, in
	test mode, when the chat is not on the allow list: a dry run, safety.py)."""
	if not is_new_enquiry(doc) or by_mobile(doc.contact_number):
		return None
	if is_ignored(doc.contact_number) or _known_contact(phone_e164(doc.contact_number)):
		return None
	return safety.crm_write(
		doc.get("chat_id") or f"{doc.contact_number}@c.us",
		"Create Lead",
		f"Created a lead for +{doc.contact_number}",
		lambda: _create_once(doc),
		once=f"lead:{doc.contact_number}",
		target_doctype="CRM Lead",
	)


def _create_once(doc) -> str | None:
	"""Find the number's lead, else create it, one job at a time per number.

	Two messages of a new customer are classified by two jobs at the same moment; both find no lead
	and both would create one. A unique index on the lead's number would stop that, but CRM Lead is
	Frappe CRM's doctype (and its users may keep two leads with one number), so it is not ours to
	add. A Redis lock per number is used instead: it is held until this job's transaction ends, so
	the next job can see the lead once it gets the lock. It looks again with a locking read, which
	sees rows committed after its own transaction began (a plain read would not)."""
	lock = frappe.cache().lock(
		frappe.cache().make_key(f"periskope_new_lead:{number_key(doc.contact_number)}"),
		timeout=LOCK_SECONDS,
		blocking_timeout=LOCK_WAIT_SECONDS,
	)
	if not lock.acquire():
		return None  # another job is still making this number's lead
	_release_at_end(lock)
	if frappe.db.get_value(
		"CRM Lead", {"periskope_phone_e164": phone_e164(doc.contact_number)}, for_update=True
	) or by_mobile(doc.contact_number):
		return None
	return _insert_lead(doc)


def _release_at_end(lock):
	"""Release the lock when the job's transaction is committed or rolled back."""

	def release():
		try:
			lock.release()
		except LockError:
			pass  # timed out, or already released by the other callback

	if frappe.flags.in_test:
		release()  # a test never commits: the lock would outlive it and block the next test
		return
	frappe.db.after_commit.add(release)
	frappe.db.after_rollback.add(release)


def _insert_lead(doc) -> str:
	first_name, last_name = split_name(contact_name(doc), doc.contact_number)
	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": first_name,
			"last_name": last_name,
			"mobile_no": f"+{doc.contact_number}",
			"source": ensure_lead_source(),
			"lead_owner": _owner(doc),
		}
	)
	# The app acts here, not a user: the customer is a WhatsApp number, and the job may run as Guest.
	lead.insert(ignore_permissions=True)

	# The new lead now also owns the earlier messages of this number.
	for name in frappe.get_all(
		"Periskope Message",
		filters={"contact_number": doc.contact_number, "is_group": 0, "link_status": ["!=", "Linked"]},
		pluck="name",
	):
		link_message(name)

	# The contact queue row (if any) is resolved by this lead.
	contact = frappe.db.get_value("Periskope Contact", {"phone": phone_e164(doc.contact_number)})
	if contact:
		frappe.db.set_value(
			"Periskope Contact", contact, {"status": "Lead Created", "linked_lead": lead.name}
		)
	return lead.name


def create_lead_for_message(message: str) -> str | None:
	"""Never raises: a problem here must not undo the message's tags."""
	try:
		frappe.db.savepoint("periskope_new_lead")
		try:
			lead = create_lead(
				frappe.db.get_value("Periskope Message", message, MESSAGE_FIELDS, as_dict=True)
			)
			if lead:
				# remembered, so a manager can take back a lead the AI should not have made
				frappe.db.set_value("Periskope Message", message, "ai_lead", lead, update_modified=False)
			return lead
		except Exception:
			frappe.db.rollback(save_point="periskope_new_lead")
			raise
	except Exception:
		frappe.log_error(title=f"Periskope: could not create a lead for message {message}")
		return None
