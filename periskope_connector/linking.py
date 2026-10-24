"""Link Periskope Messages to CRM leads and deals.

How a message finds its lead or deal:
- 1-to-1 chat: the customer's mobile number, on a deal first and then on a lead.
- Customer group (a group about one customer): the group name, then the sender's mobile number.
- Internal group (a team group): the customer name the AI finds in the message text.
- A lead or deal set by hand on the Periskope Group always wins.

Only a link is saved here. Nothing in the CRM is changed.
"""

import re

import frappe

from periskope_connector.client import fetch_chat_name
from periskope_connector.system import insert_as_system
from periskope_connector.timeline_comments import add_comment

MIN_NAME_LENGTH = 5
NUMBER_LENGTH = 10  # compare the last 10 digits, so "+91 98765 43210" matches "9876543210"

# A deal whose status type is one of these is not an active conversation (CRM Deal Status.type).
CLOSED_DEAL_TYPES = {"Won", "Lost"}

COMPANY_WORDS = {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "company", "and"}


# --- Matching helpers -------------------------------------------------------------------------------


def number_key(value: str | None) -> str:
	digits = re.sub(r"\D", "", value or "")
	return digits[-NUMBER_LENGTH:] if len(digits) >= NUMBER_LENGTH else ""


def phone_e164(value: str | None) -> str:
	"""The full number as stored by WhatsApp (digits with country code, no '+'): '919876512340'.
	Unlike the last-10 key this keeps the country code, so foreign buyers cannot collide with
	Indian numbers that share trailing digits."""
	return re.sub(r"\D", "", value or "")


def name_key(value: str | None) -> str:
	words = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower()).split()
	return " ".join(w for w in words if w not in COMPANY_WORDS)


def names_match(a: str | None, b: str | None) -> bool:
	"""Same company name, ignoring case, punctuation and 'Pvt Ltd'. One name inside the other also
	counts ('Sumi Export House - Tirupur' and 'Sumi Export House') when it is long enough."""
	ka, kb = name_key(a), name_key(b)
	if not ka or not kb:
		return False
	if ka == kb:
		return True
	short, long_ = sorted((ka, kb), key=len)
	return len(short) >= MIN_NAME_LENGTH and f" {short} " in f" {long_} "


# A candidate is (lead, deal). A converted lead is represented by its deal.


def _from_deals(rows) -> list[tuple]:
	return [(r.lead, r.name) for r in rows]


def _from_leads(rows) -> list[tuple]:
	found = []
	for r in rows:
		deal = frappe.db.get_value("CRM Deal", {"lead": r.name}, "name") if r.converted else None
		found.append((r.name, deal))
	return found


def by_mobile(number: str | None) -> list[tuple]:
	"""Leads and deals whose phone matches, found through the indexed normalised fields
	(periskope_phone_e164 / periskope_phone_key) that the app maintains on every lead and deal.
	The full number is tried first, so a foreign buyer never falls back to the last-10 key."""
	full = phone_e164(number)
	if not full:
		return []
	for field, value in (
		("periskope_phone_e164", full),
		("periskope_phone_key", number_key(number)),
	):
		if not value:
			continue
		deals = frappe.get_all("CRM Deal", filters={field: value}, fields=["name", "lead"], limit=50)
		leads = frappe.get_all("CRM Lead", filters={field: value}, fields=["name", "converted"], limit=50)
		if deals or leads:
			return _from_deals(deals) + _from_leads(leads)
	return []


def update_phone_fields(doc, method: str | None = None):
	"""Keep the normalised, indexed phone fields of a lead or deal in step with mobile_no / phone.

	Runs as a doc_event on every save; a failure here must never stop a sales save.
	"""
	try:
		value = phone_e164(doc.mobile_no or doc.phone)
		frappe.db.set_value(
			doc.doctype,
			doc.name,
			{
				"periskope_phone_e164": value,
				"periskope_phone_key": number_key(value) if value else "",
			},
			update_modified=False,
		)
	except Exception:
		frappe.log_error(title=f"Periskope: could not set the phone fields of {doc.doctype} {doc.name}")


def backfill_phone_fields(doctype: str, page: int = 500) -> int:
	"""Fill the normalised fields from existing mobile_no / phone values (install and migrate run it,
	see install._ensure_phone_fields). Returns how many records were filled.
	Run with: bench --site <site> execute periskope_connector.linking.backfill_phone_fields --args '["CRM Lead"]'
	"""
	total = 0
	seen: set[str] = set()
	while True:
		rows = frappe.get_all(
			doctype,
			filters=[["periskope_phone_e164", "is", "not set"]],
			or_filters=[["mobile_no", "is", "set"], ["phone", "is", "set"]],
			fields=["name", "mobile_no", "phone"],
			limit_page_length=page,
			order_by="creation asc",
		)
		# A phone with no digits at all (a dash, a note) can never gain a normalised value, so it
		# stays 'not set' forever; the seen-guard stops instead of looping over it endlessly.
		if not rows or all(r.name in seen for r in rows):
			return total
		for row in rows:
			if row.name in seen:
				continue
			seen.add(row.name)
			value = phone_e164(row.mobile_no or row.phone)
			frappe.db.set_value(
				doctype,
				row.name,
				{"periskope_phone_e164": value, "periskope_phone_key": number_key(value)},
				update_modified=False,
			)
			total += 1
		frappe.db.commit()


def own_companies() -> list[str]:
	return frappe.get_all("Company", pluck="name") if frappe.db.exists("DocType", "Company") else []


def organizations() -> list[str]:
	"""Company names used by leads and deals, except our own company: a group called
	'Pranera ~ Some Customer' is about the customer, not about us."""
	own = own_companies()
	names = [
		r[0]
		for r in frappe.db.sql(
			"""select distinct organization from `tabCRM Lead` where ifnull(organization, '') != ''
			union select distinct organization from `tabCRM Deal` where ifnull(organization, '') != ''"""
		)
	]
	return [n for n in names if not any(names_match(n, company) for company in own)]


def by_organization(name: str | None) -> list[tuple]:
	matching = [o for o in organizations() if names_match(o, name)]
	if not matching:
		return []
	deals = frappe.get_all("CRM Deal", filters={"organization": ["in", matching]}, fields=["name", "lead"])
	leads = frappe.get_all(
		"CRM Lead", filters={"organization": ["in", matching]}, fields=["name", "converted"]
	)
	return _from_deals(deals) + _from_leads(leads)


def _status_types() -> dict[str, str]:
	"""CRM deal status name -> its type (Open / Ongoing / On Hold / Won / Lost). Small table."""
	try:
		return dict(frappe.get_all("CRM Deal Status", fields=["name", "type"], as_list=True))
	except Exception:
		return {}


def _pick(candidates: list[tuple]) -> tuple:
	"""The best candidate: open (not Won/Lost) deals beat closed ones, then the newest edit.

	Candidates here all have a deal (resolve prefers deals first)."""
	types = _status_types()
	info = {
		row.name: row
		for row in frappe.get_all(
			"CRM Deal",
			filters={"name": ["in", [d for _l, d in candidates if d]]},
			fields=["name", "lead", "modified", "status"],
		)
	}

	def rank(candidate):
		deal = info.get(candidate[1])
		# open deals sort highest, so max() picks them first despite a newer closed edit
		open_ = 0 if deal and types.get(deal.status) in CLOSED_DEAL_TYPES else 1
		return (open_, str(deal.modified) if deal else "")

	return max(candidates, key=rank)


def resolve(candidates: list[tuple]) -> tuple[str | None, str | None, str]:
	"""(lead, deal, status). A deal beats a lead; open deals are considered before closed ones,
	and among equals the newest edit wins. A closed match only wins when no open one exists.
	Several different open matches use the best one and are flagged Ambiguous for review."""
	unique = list(dict.fromkeys(c for c in candidates if c[0] or c[1]))
	if not unique:
		return None, None, "No Match"

	deal_names = [d for _l, d in unique if d]
	deal_rows = {}
	if deal_names:
		deal_rows = {
			row.name: row
			for row in frappe.get_all(
				"CRM Deal", filters={"name": ["in", deal_names]}, fields=["name", "lead", "status"]
			)
		}
	# A lead already inside a matched deal is the same customer, not a second match.
	unique = [
		c
		for c in unique
		if c[1] or not any(deal_rows[d].lead == c[0] for d in deal_rows if deal_rows[d].lead)
	]
	if not unique:
		return None, None, "No Match"

	with_deals = [c for c in unique if c[1]]
	if with_deals:
		# Open deals first: a closed deal from an old order must not shadow the live one.
		# Ambiguous only among open deals — that is the case a manager must review. Falling back
		# to closed ones picks the newest and links to it.
		types = _status_types()
		open_deals = [c for c in with_deals if types.get(deal_rows[c[1]].status) not in CLOSED_DEAL_TYPES]
		if open_deals:
			lead, deal = _pick(open_deals) if len(open_deals) > 1 else open_deals[0]
			return lead, deal, "Linked" if len(open_deals) == 1 else "Ambiguous"
		lead, deal = _pick(with_deals)
		return lead, deal, "Linked"

	lead = _newest_lead([c[0] for c in unique])
	return lead, None, "Linked" if len(unique) == 1 else "Ambiguous"


def _newest_lead(lead_names: list[str]) -> str | None:
	if len(lead_names) == 1:
		return lead_names[0]
	rows = frappe.get_all("CRM Lead", filters={"name": ["in", lead_names]}, fields=["name", "modified"])
	if not rows:
		return lead_names[0]
	return str(max(rows, key=lambda r: str(r.modified)).name)


# --- Groups ------------------------------------------------------------------------------------------


def ensure_group(chat_id: str, phone: str | None = None):
	"""The Periskope Group of this chat, created on first sight with its name and type."""
	name = frappe.db.get_value("Periskope Group", {"chat_id": chat_id})
	if name:
		group = frappe.db.get_value(
			"Periskope Group", name, ["name", "chat_id", "group_name", "group_type"], as_dict=True
		)
		if not group.group_name:
			group_name = fetch_chat_name(chat_id, phone)
			if group_name:
				frappe.db.set_value("Periskope Group", name, "group_name", group_name)
				group.group_name = group_name
		return group

	group_name = fetch_chat_name(chat_id, phone)
	group = frappe.get_doc(
		{
			"doctype": "Periskope Group",
			"chat_id": chat_id,
			"group_name": group_name,
			# A group named after a known customer is a customer group; anything else is a team group.
			"group_type": "Customer" if group_name and by_organization(group_name) else "Internal",
		}
	)
	try:
		insert_as_system(group)
	except frappe.DuplicateEntryError:
		frappe.db.rollback()
		return frappe.db.get_value(
			"Periskope Group",
			{"chat_id": chat_id},
			["name", "chat_id", "group_name", "group_type"],
			as_dict=True,
		)
	return group


# --- Linking -----------------------------------------------------------------------------------------


# The message fields that decide its CRM link.
LINK_FIELDS = ["is_group", "contact_number", "chat_id", "sender_phone", "mentioned_customer"]


def find_link(doc) -> tuple[str | None, str | None, str, str]:
	"""(lead, deal, status, method) for a Periskope Message, or a status with no lead/deal."""
	if not doc.is_group:
		# A lead or deal chosen by hand in the inbox wins over the match by number.
		manual = frappe.db.get_value(
			"Periskope Chat",
			{"chat_id": doc.chat_id, "linked_by_hand": 1},
			["crm_lead", "crm_deal"],
			as_dict=True,
		)
		if manual and (manual.crm_lead or manual.crm_deal):
			return manual.crm_lead, manual.crm_deal, "Linked", "Chosen in the Inbox"
		lead, deal, status = resolve(by_mobile(doc.contact_number))
		return lead, deal, status, "Mobile Number"

	group = frappe.db.get_value(
		"Periskope Group",
		{"chat_id": doc.chat_id},
		["group_name", "group_type", "crm_lead", "crm_deal", "organization"],
		as_dict=True,
	)
	if group and (group.crm_lead or group.crm_deal):
		return group.crm_lead, group.crm_deal, "Linked", "Group Mapping"
	if group and group.organization:
		# mapped to a company: its deals and leads
		deals = frappe.get_all(
			"CRM Deal", filters={"organization": group.organization}, fields=["name", "lead"]
		)
		leads = frappe.get_all(
			"CRM Lead", filters={"organization": group.organization}, fields=["name", "converted"]
		)
		lead, deal, status = resolve(_from_deals(deals) + _from_leads(leads))
		if status != "No Match":
			return lead, deal, status, "Group Mapping"

	if group and group.group_type == "Customer":
		lead, deal, status = resolve(by_organization(group.group_name))
		if status != "No Match":
			return lead, deal, status, "Group Name"
		lead, deal, status = resolve(by_mobile(doc.sender_phone))
		return lead, deal, status, "Mobile Number"

	# Internal team group: the customer is named in the message text.
	if doc.mentioned_customer:
		lead, deal, status = resolve(by_organization(doc.mentioned_customer))
		if status != "No Match":
			return lead, deal, status, "Customer Name"
	return None, None, "Internal", ""


def link_message(message: str) -> str:
	"""Work out and save the CRM link of one message. Returns the link status.

	Never raises: a problem here must not stop a message from being saved or tagged."""
	try:
		doc = frappe.db.get_value("Periskope Message", message, LINK_FIELDS, as_dict=True)
		lead, deal, status, method = find_link(doc)
		values = {"crm_lead": lead, "crm_deal": deal, "link_status": status, "link_method": method}
		if status in ("Linked", "Ambiguous"):
			values["attachment_not_on_crm"] = ""  # the file goes on the record now
		frappe.db.set_value("Periskope Message", message, values, update_modified=False)
		if status in ("Linked", "Ambiguous"):
			add_comment(message)
			from periskope_connector.attachments import attach_to_record

			attach_to_record(message)
		_after_link(message)
		return status
	except Exception:
		frappe.log_error(title=f"Periskope: could not link message {message} to the CRM")
		return ""


CHAT_FIELDS = [
	"name",
	"chat_id",
	"whatsapp_number",
	"periskope_chat",
	"agent_reply",
	"body",
	"message_type",
	"transcript",
	"is_deleted",
	"media_filename",
	"chat_name",
	"sender_name",
	"direction",
	"is_group",
	"contact_number",
	"org_phone",
	"message_timestamp",
	"crm_lead",
	"crm_deal",
]


def _after_link(message: str):
	"""Keep the chat row and the CRM WhatsApp tab in step with the message's link."""
	from periskope_connector import chat, mirror

	row = frappe.db.get_value("Periskope Message", message, CHAT_FIELDS, as_dict=True)
	if row and row.chat_id:
		chat.ensure(row)
		mirror.upsert(message)


def link_unlinked(limit: int = 5000) -> int:
	"""Link messages that have no link yet or could not be matched earlier.
	Run with: bench --site <site> execute periskope_connector.linking.link_unlinked"""
	names = frappe.get_all(
		"Periskope Message",
		or_filters=[["link_status", "is", "not set"], ["link_status", "in", ["No Match", "Internal"]]],
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)
	for name in names:
		link_message(name)
	return len(names)
