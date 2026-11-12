"""Link Periskope Messages to CRM leads and deals.

How a message finds its lead or deal:
- 1-to-1 chat: the customer's mobile number, on a deal first and then on a lead.
- Customer group (a group about one customer): the group name, then the sender's mobile number.
- Internal group (a team group): the customer name the AI finds in the message text. A group taken
  for a team group whose member is a known customer contact becomes that customer's group.
- A lead or deal set by hand on the Periskope Group always wins.

Only a link is saved here. Nothing in the CRM is changed.
"""

import re
from difflib import SequenceMatcher

import frappe
from frappe.utils.caching import redis_cache

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


def home_code() -> str:
	"""The country code of the company's country (System Settings), for numbers typed without one."""
	if "periskope_home_code" not in frappe.local.cache:
		from frappe.geo.country_info import get_country_info

		country = frappe.db.get_single_value("System Settings", "country") or "India"
		isd = (get_country_info(country) or {}).get("isd") or "+91"
		frappe.local.cache["periskope_home_code"] = re.sub(r"\D", "", isd) or "91"
	return frappe.local.cache["periskope_home_code"]


def with_country_code(value: str | None) -> str:
	"""A number as WhatsApp writes it: digits with the country code. A local number (10 digits, or 11
	with a leading 0) gets the company's country code."""
	digits = re.sub(r"\D", "", value or "")
	if len(digits) == NUMBER_LENGTH + 1 and digits.startswith("0"):
		digits = digits[1:]
	return home_code() + digits if len(digits) == NUMBER_LENGTH else digits


def _singular(word: str) -> str:
	"""'apparels' -> 'apparel', 'exports' -> 'export'; 'glass' and short words stay as they are."""
	return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def name_key(value: str | None) -> str:
	"""A company name for comparing: lower case, no punctuation or company words, plurals folded, so
	'Mg Apparel - Pranera' and 'MG APPARELS PVT LTD' share 'mg apparel'."""
	words = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower()).split()
	return " ".join(_singular(w) for w in words if w not in COMPANY_WORDS)


def names_match(a: str | None, b: str | None) -> bool:
	"""Same company name, ignoring case, punctuation and 'Pvt Ltd'. One name inside the other also
	counts ('Sumi Export House - Tirupur' and 'Sumi Export House') when it is long enough."""
	ka, kb = name_key(a), name_key(b)
	if not ka or not kb:
		return False
	if ka == kb:
		return True
	short, long_ = sorted((ka, kb), key=len)
	if len(short) < MIN_NAME_LENGTH or f" {short} " not in f" {long_} ":
		return False
	# One word alone counts only as the start of the longer name: 'Innovative - Pranera' is
	# Innovative's, but 'Textiles - Garment Sales' is not the company called 'Garments'.
	return " " in short or long_.startswith(short + " ")


# Words any textile company may have in its name: sharing one says nothing about who it is.
TRADE_WORDS = {
	"apparel", "cloth", "clothing", "cotton", "enterprise", "export", "exim", "fabric", "fashion",
	"garment", "group", "impex", "import", "india", "industry", "industrie", "international", "knit",
	"mill", "solution", "spinning", "textile", "trading", "trader", "wear", "weaving",
}  # fmt: skip


def _words(name: str | None, own: set[str]) -> set[str]:
	return {w for w in name_key(name).split() if w not in TRADE_WORDS and w not in own}


def resembles(organization: str | None, group_name: str | None) -> bool:
	"""Whether a group's name points at a company: they share a telling word ('MG', 'Tejwaney') or one
	spelt a little differently ('Corpqlo' for 'Coroqlo'). Our own company's words do not count."""
	own = {w for company in own_companies() for w in name_key(company).split()}
	theirs, ours = _words(organization, own), _words(group_name, own)
	if theirs & ours:
		return True
	return any(
		len(a) >= MIN_NAME_LENGTH and len(b) >= MIN_NAME_LENGTH and SequenceMatcher(None, a, b).ratio() >= 0.8
		for a in theirs
		for b in ours
	)


# A candidate is (lead, deal). A converted lead is represented by its deal.


def _from_deals(rows) -> list[tuple]:
	return [(r.lead, r.name) for r in rows]


def _from_leads(rows) -> list[tuple]:
	# the deals of the converted leads in one query, not one per lead
	converted = [r.name for r in rows if r.converted]
	deal_of = {}
	if converted:
		for lead, deal in frappe.get_all(
			"CRM Deal", filters={"lead": ["in", converted]}, fields=["lead", "name"], as_list=True
		):
			deal_of.setdefault(lead, deal)
	return [(r.name, deal_of.get(r.name)) for r in rows]


def number_forms(number: str | None) -> list[str]:
	"""The ways the CRM may have saved a number: in full with the country code, and for a number of
	the company's own country also without it ("9876543210", "09876543210")."""
	full = with_country_code(number)
	if not full:
		return []
	forms = [full]
	local = full[len(home_code()) :] if full.startswith(home_code()) else ""
	if len(local) == NUMBER_LENGTH:
		forms += [local, "0" + local, "0" + full]
	return forms


def by_mobile(number: str | None) -> list[tuple]:
	"""Leads and deals whose phone matches, found through the indexed normalised field
	periskope_phone_e164 that the app maintains on every lead and deal. The full number matches as it
	is; a number of the company's own country also matches where the CRM saved it without the country
	code ("9876543210", "09876543210"). Nothing else is compared by its last digits, so a foreign buyer
	never meets a local lead that shares them."""
	forms = number_forms(number)
	if not forms:
		return []
	found = []
	# the mobile number, then a second number in the Phone field (periskope_phone2_e164)
	for field in ("periskope_phone_e164", "periskope_phone2_e164"):
		filters = {field: ["in", forms]}
		deals = frappe.get_all("CRM Deal", filters=filters, fields=["name", "lead"], limit=50)
		leads = frappe.get_all("CRM Lead", filters=filters, fields=["name", "converted"], limit=50)
		found += [c for c in _from_deals(deals) + _from_leads(leads) if c not in found]
	return found


def phone_values(mobile_no: str | None, phone: str | None) -> dict:
	"""The normalised phone fields of a lead or deal: its main number, and the Phone field as a second
	number when both are set and differ."""
	main = phone_e164(mobile_no or phone)
	second = phone_e164(phone) if mobile_no and phone else ""
	return {
		"periskope_phone_e164": main,
		"periskope_phone_key": number_key(main) if main else "",
		"periskope_phone2_e164": second if second and second != main else "",
	}


def update_phone_fields(doc, method: str | None = None):
	"""Keep the normalised, indexed phone fields of a lead or deal in step with mobile_no / phone.
	A number new on the record links its past WhatsApp messages (relink_number, in the background).

	Runs as a doc_event on every save; a failure here must never stop a sales save. A save may add or
	change an organization, so the cached list of organizations is dropped too.
	"""
	frappe.cache().delete_value(ORGANIZATIONS_KEY)
	try:
		values = phone_values(doc.mobile_no, doc.phone)
		before = (
			frappe.db.get_value(
				doc.doctype, doc.name, ["periskope_phone_e164", "periskope_phone2_e164"], as_dict=True
			)
			or {}
		)
		frappe.db.set_value(doc.doctype, doc.name, values, update_modified=False)
		new = {values["periskope_phone_e164"], values["periskope_phone2_e164"]} - {
			before.get("periskope_phone_e164"),
			before.get("periskope_phone2_e164"),
			"",
			None,
		}
		for number in new:
			frappe.enqueue(
				"periskope_connector.linking.relink_number",
				number=number,
				queue="long",
				enqueue_after_commit=True,
				job_id=f"periskope_relink_number::{number}",
				deduplicate=True,
			)
	except Exception:
		frappe.log_error(title=f"Periskope: could not set the phone fields of {doc.doctype} {doc.name}")


def relink_number(number: str) -> int:
	"""Background job: a lead or deal got this number. Its 1-to-1 chats' messages are linked again,
	and its row in the contact queue is marked linked. Returns how many messages were linked."""
	full = with_country_code(number)
	if not full:
		return 0
	linked = 0
	for chat_id in frappe.get_all(
		"Periskope Message", filters={"contact_number": full, "is_group": 0}, pluck="chat_id", distinct=True
	):
		if not frappe.db.exists("Periskope Chat", {"chat_id": chat_id, "linked_by_hand": 1}):
			linked += relink_messages(chat_id)
	lead, deal, status = resolve(by_mobile(full))
	if status in ("Linked", "Ambiguous"):
		frappe.db.set_value(
			"Periskope Contact",
			{"phone": full, "status": "New"},
			{"status": "Linked", "linked_lead": lead, "linked_deal": deal},
		)
	return linked


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
			frappe.db.set_value(
				doctype, row.name, phone_values(row.mobile_no, row.phone), update_modified=False
			)
			total += 1
		frappe.db.commit()


def own_companies() -> list[str]:
	return frappe.get_all("Company", pluck="name") if frappe.db.exists("DocType", "Company") else []


ORGANIZATIONS_KEY = "periskope:organizations"
ORGANIZATIONS_SECONDS = 600


def organizations() -> list[str]:
	"""Company names used by leads and deals, except our own company: a group called
	'Pranera ~ Some Customer' is about the customer, not about us. Every group message needs this
	list, so it is kept for 10 minutes and dropped whenever a lead or deal is saved."""
	cache = frappe.cache()
	names = cache.get_value(ORGANIZATIONS_KEY, expires=True)
	if names is None:
		names = _organizations()
		cache.set_value(ORGANIZATIONS_KEY, names, expires_in_sec=ORGANIZATIONS_SECONDS)
	return names


def _organizations() -> list[str]:
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
	frappe.db.savepoint("periskope_new_group")
	try:
		insert_as_system(group)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		# Saved by another message of the group at the same moment: undo only this insert (the
		# caller's own work stays) and use that row; a locking read sees it though it is new.
		frappe.db.rollback(save_point="periskope_new_group")
		frappe.clear_last_message()
		return frappe.db.get_value(
			"Periskope Group",
			{"chat_id": chat_id},
			["name", "chat_id", "group_name", "group_type"],
			as_dict=True,
			for_update=True,
		)
	return group


def refresh_group_name(chat_id: str, name: str) -> bool:
	"""A group renamed in WhatsApp: the stored group and its chat rows take the new name, and a group
	matched by its name (not set up by hand) is linked again in the background. Returns True when the
	name changed. Never raises."""
	try:
		name = (name or "").strip()
		group = frappe.db.get_value(
			"Periskope Group",
			{"chat_id": chat_id},
			["name", "group_name", "organization", "crm_lead", "crm_deal"],
			as_dict=True,
		)
		if not group or not name or group.group_name == name:
			return False
		frappe.db.set_value("Periskope Group", group.name, "group_name", name)
		for row in frappe.get_all("Periskope Chat", filters={"chat_id": chat_id}, pluck="name"):
			frappe.db.set_value("Periskope Chat", row, "chat_name", name, update_modified=False)
		if not (group.organization or group.crm_lead or group.crm_deal):
			frappe.enqueue(
				"periskope_connector.linking.relink_messages",
				chat_id=chat_id,
				queue="long",
				enqueue_after_commit=True,
			)
		return True
	except Exception:
		frappe.log_error(title=f"Periskope: could not rename group {chat_id}")
		return False


# --- Group members -----------------------------------------------------------------------------------


@redis_cache(ttl=3600)
def staff_numbers() -> set[str]:
	"""Our own people's numbers (users, active employees, our WhatsApp numbers), with the country
	code. A colleague's number may also sit on a customer's contact, so it never tells whose group
	it is."""
	numbers = set(frappe.get_all("WhatsApp Number", pluck="name"))
	for row in frappe.get_all(
		"User", filters={"enabled": 1, "user_type": "System User"}, fields=["mobile_no", "phone"]
	):
		numbers.update([row.mobile_no, row.phone])
	if frappe.db.exists("DocType", "Employee"):
		numbers.update(frappe.get_all("Employee", filters={"status": "Active"}, pluck="cell_number"))
	return {with_country_code(n) for n in numbers if n} - {""}


def member_organizations(number: str | None) -> set[str]:
	"""The CRM organizations a group member's number belongs to: through a lead or deal with that
	number, or a deal that lists a contact with that number. Empty for our own people."""
	forms = number_forms(number)
	if not forms or forms[0] in staff_numbers():
		return set()
	set_ = ["is", "set"]
	by_phone = {"periskope_phone_e164": ["in", forms], "organization": set_}
	found = set(frappe.get_all("CRM Deal", filters=by_phone, pluck="organization", limit=20))
	found |= set(frappe.get_all("CRM Lead", filters=by_phone, pluck="organization", limit=20))
	contacts = frappe.get_all(
		"Contact Phone",
		filters={"parenttype": "Contact", "phone": ["in", [*forms, "+" + forms[0]]]},
		pluck="parent",
		limit=20,
	)
	if contacts:
		deals = frappe.get_all(
			"CRM Contacts", filters={"parenttype": "CRM Deal", "contact": ["in", contacts]}, pluck="parent"
		)
		if deals:
			found |= set(
				frappe.get_all(
					"CRM Deal", filters={"name": ["in", deals], "organization": set_}, pluck="organization"
				)
			)
	own = own_companies()
	return {o for o in found if not any(names_match(o, company) for company in own)}


def map_group_by_member(chat_id: str, number: str | None) -> str | None:
	"""A group taken for a team group (no organization, lead or deal) that has a member from exactly
	one customer organization, and whose name resembles that organization, is that customer's group:
	it becomes a Customer group linked to it. A member alone is not enough: a forwarder or a supplier
	in a team group is a contact on someone's deal too (the AI is shown such companies as candidates,
	and a manager decides). Returns the organization, or None when nothing changed. Never raises: a
	message must be saved even if this fails."""
	try:
		group = frappe.db.get_value(
			"Periskope Group",
			{"chat_id": chat_id},
			["name", "group_name", "group_type", "organization", "crm_lead", "crm_deal"],
			as_dict=True,
		)
		if (
			not group
			or group.group_type != "Internal"
			or group.organization
			or group.crm_lead
			or group.crm_deal
		):
			return None
		found = member_organizations(number)
		if len(found) != 1:
			return None  # unknown, our own person, or more than one company: leave it to people
		organization = found.pop()
		if not resembles(organization, group.group_name):
			return None
		from periskope_connector.api.crm_panel import set_group_organization

		set_group_organization(chat_id, group.name, organization)
		return organization
	except Exception:
		frappe.log_error(title=f"Periskope: could not map group {chat_id} by its member")
		return None


def map_groups(limit: int = 500) -> int:
	"""Team groups (no organization, lead or deal) whose name now matches a customer, or whose member
	is a customer contact, become that customer's group. Returns how many changed.
	Run with: bench --site <site> execute periskope_connector.linking.map_groups"""
	groups = frappe.get_all(
		"Periskope Group",
		filters={
			"group_type": "Internal",
			"organization": ["is", "not set"],
			"crm_lead": ["is", "not set"],
			"crm_deal": ["is", "not set"],
		},
		fields=["name", "chat_id", "group_name"],
		limit=limit,
	)
	changed = 0
	for group in groups:
		if group.group_name and by_organization(group.group_name):
			frappe.db.set_value("Periskope Group", group.name, "group_type", "Customer")
			relink_messages(group.chat_id)
			changed += 1
			continue
		senders = frappe.get_all(
			"Periskope Message",
			filters={"chat_id": group.chat_id, "direction": "Incoming", "sender_phone": ["is", "set"]},
			pluck="sender_phone",
			distinct=True,
			limit=50,
		)
		if any(map_group_by_member(group.chat_id, number) for number in senders):
			changed += 1
	return changed


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
		if status in ("Linked", "Ambiguous") and not frappe.flags.periskope_history:
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
	"link_status",
]


def _after_link(message: str):
	"""Keep the chat row in step with the message's link."""
	from periskope_connector import chat

	row = frappe.db.get_value("Periskope Message", message, CHAT_FIELDS, as_dict=True)
	if row and row.chat_id:
		chat.ensure(row)


def relink_chats(chat_id: str, values: dict):
	"""A link changed by hand (a chat linked or unlinked in the inbox, a group mapped to a customer or
	an organization): every chat row of the contact or group gets `values` at once, and its messages
	are linked again. Only the newest message is linked here, which is what the inbox shows next; a
	chat can hold thousands, so the rest follow in a background job."""
	from periskope_connector import chat

	chat.set_on_all(chat_id, values)
	if frappe.flags.in_test:
		relink_messages(chat_id)  # a test has no worker: the whole chat, now
		return
	newest = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": chat_id},
		pluck="name",
		order_by="message_timestamp desc",
		limit=1,
	)
	if newest:
		link_message(newest[0])
	frappe.enqueue(
		"periskope_connector.linking.relink_messages",
		chat_id=chat_id,
		queue="long",
		enqueue_after_commit=True,
	)


def relink_messages(chat_id: str) -> int:
	"""Link every message of a contact's (or group's) chats again. Returns how many."""
	names = frappe.get_all("Periskope Message", filters={"chat_id": chat_id}, pluck="name")
	for name in names:
		link_message(name)
	return len(names)


def group_target(group) -> tuple[str | None, str | None]:
	"""(lead, deal) a mapped group's chats show: the lead or deal it is mapped to, else the best deal
	or lead of its organization."""
	if group.crm_lead or group.crm_deal:
		return group.crm_lead, group.crm_deal
	if not group.organization:
		return None, None
	deals = frappe.get_all("CRM Deal", filters={"organization": group.organization}, fields=["name", "lead"])
	leads = frappe.get_all(
		"CRM Lead", filters={"organization": group.organization}, fields=["name", "converted"]
	)
	lead, deal, _status = resolve(_from_deals(deals) + _from_leads(leads))
	return lead, deal


def relink_group(group: str, user: str | None = None):
	"""A group's mapping was saved: its chat rows show the customer (and that it was chosen by hand),
	and its messages are linked again."""
	row = frappe.db.get_value(
		"Periskope Group", group, ["chat_id", "crm_lead", "crm_deal", "organization"], as_dict=True
	)
	lead, deal = group_target(row)
	mapped = bool(lead or deal or row.organization)
	relink_chats(
		row.chat_id,
		{
			"organization": row.organization,
			"crm_lead": lead,
			"crm_deal": deal,
			"linked_by_hand": 1 if mapped else 0,
			"linked_by": user if mapped else None,
		},
	)


def link_unlinked(limit: int = 5000, days: int | None = None) -> int:
	"""Link messages that have no link yet or could not be matched earlier (a lead made since, a
	number added in the CRM). Scheduler, daily: the last LINK_AGAIN_DAYS days; by hand, everything:
	bench --site <site> execute periskope_connector.linking.link_unlinked"""
	filters = {"creation": [">=", frappe.utils.add_days(frappe.utils.now_datetime(), -days)]} if days else {}
	names = frappe.get_all(
		"Periskope Message",
		filters=filters,
		or_filters=[["link_status", "is", "not set"], ["link_status", "in", ["No Match", "Internal"]]],
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)
	for name in names:
		link_message(name)
	return len(names)


LINK_AGAIN_DAYS = 30


def link_unlinked_daily() -> int:
	"""Scheduler, daily: link_unlinked over the last LINK_AGAIN_DAYS days."""
	return link_unlinked(limit=3000, days=LINK_AGAIN_DAYS)
