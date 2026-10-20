"""Tasks handed out in group chats become CRM Tasks.

A team group message like "@9170.. finish the GSM roll return for Arrow Export today, @9190.. send
1300 kg to GST Sourcing" holds several jobs. The AI that tags the message lists them (classifier,
the "tasks" field); here each one becomes a CRM Task:

- on the customer's record: the organization named for that task, matched loosely ("GST Sourcing"
  finds "GST SORUCING", "Arrow Export" finds "ARROW EXPORTS"), its open deal first, then its lead,
- assigned to the person @mentioned for it (found by the mobile number on their User), else the
  record's owner, else the sender,
- due on the day the message gives, at the end of the working day, else today (tomorrow after
  hours).

A task whose customer cannot be told apart, or is not in the CRM, is still created without a link and
says what to check. Each message makes its tasks once (reclassifying it does not repeat them), and
every write goes through safety.crm_write, so test mode keeps group chats a dry run.
"""

import json
import re
from difflib import SequenceMatcher

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, getdate, now_datetime

from periskope_connector import linking, safety, writeback
from periskope_connector.system import insert_as_system

MAX_TASKS = 10  # per message
FUZZY = 0.85  # name similarity from which a misspelt name is the same company ("sorucing")
CONTAINED = 0.9  # score of a name found inside a longer one ("balu export" in "balu export palladam")
MESSAGE_FIELDS = [
	"name",
	"chat_id",
	"chat_name",
	"body",
	"sender_phone",
	"severity",
	"is_group",
	"raw_payload",
]


# --- Customer ----------------------------------------------------------------------------------------


def _key(name: str | None) -> str:
	"""linking.name_key, with plurals folded: "Arrow Export" and "ARROW EXPORTS" are one name."""
	return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in linking.name_key(name).split())


def _score(wanted: str, candidate: str) -> float:
	a, b = _key(wanted), _key(candidate)
	if not a or not b:
		return 0.0
	if a == b:
		return 1.0
	short, long_ = sorted((a, b), key=len)
	if len(short) >= linking.MIN_NAME_LENGTH and f" {short} " in f" {long_} ":
		return CONTAINED
	ratio = SequenceMatcher(None, a, b).ratio()
	return ratio if ratio >= FUZZY else 0.0


def organizations_like(name: str | None) -> list[str]:
	"""The CRM organizations that best match a customer name: the closest ones, or none."""
	scored = [(s, org) for org in linking.organizations() if (s := _score(name, org))]
	if not scored:
		return []
	best = max(s for s, _org in scored)
	return [org for s, org in scored if s == best]


def find_record(customer: str | None, assignee: str | None = None) -> tuple[tuple | None, str]:
	"""((doctype, name) or None, a note for the rep when the match needs checking)."""
	if not (customer or "").strip():
		return None, ""
	orgs = organizations_like(customer)
	if not orgs:
		return None, _("No customer called {0} was found in the CRM.").format(customer)
	# These exact organizations: linking.by_organization would widen "BALU EXPORTS" to every name
	# containing it again.
	deals = frappe.get_all("CRM Deal", filters={"organization": ["in", orgs]}, fields=["name", "lead"])
	leads = frappe.get_all("CRM Lead", filters={"organization": ["in", orgs]}, fields=["name", "converted"])
	candidates = linking._from_deals(deals) + linking._from_leads(leads)
	if assignee:
		# Of several deals of the customer, the one the assignee works on.
		owned = [c for c in candidates if _owner(c) == assignee]
		candidates = owned or candidates
	lead, deal, status = linking.resolve(candidates)
	record = ("CRM Deal", deal) if deal else (("CRM Lead", lead) if lead else None)
	if not record:
		return None, _("{0} was found, but it has no lead or deal.").format(", ".join(orgs))
	if status == "Ambiguous":
		return record, _("{0} has more than one open deal; check this is the right one.").format(customer)
	return record, ""


def _owner(candidate: tuple) -> str | None:
	lead, deal = candidate
	if deal:
		return frappe.db.get_value("CRM Deal", deal, "deal_owner")
	return frappe.db.get_value("CRM Lead", lead, "lead_owner")


# --- People -------------------------------------------------------------------------------------------


def user_for_phone(number: str | None) -> str | None:
	"""The one enabled user whose mobile or phone is this number."""
	key = linking.number_key(number)
	if not key:
		return None
	users = frappe.get_all(
		"User",
		filters={"enabled": 1, "name": ["not in", ["Administrator", "Guest"]]},
		or_filters=[["mobile_no", "like", f"%{key}"], ["phone", "like", f"%{key}"]],
		pluck="name",
		limit=2,
	)
	return users[0] if len(users) == 1 else None


def mentioned_numbers(doc) -> set[str]:
	"""The last-10 keys of the numbers @mentioned in the message, so the AI cannot invent one."""
	numbers = re.findall(r"@(\d{6,})", doc.body or "")
	try:
		numbers += [
			str(m).split("@")[0] for m in json.loads(doc.raw_payload or "{}").get("mentioned_ids") or []
		]
	except (ValueError, AttributeError):
		pass
	return {k for n in numbers if (k := linking.number_key(n))}


def _assignee(item: dict, doc, record: tuple | None) -> str | None:
	phone = linking.number_key(item.get("assignee_phone"))
	if phone and phone in mentioned_numbers(doc):
		user = user_for_phone(phone)
		if user:
			return user
	if record:
		field = "deal_owner" if record[0] == "CRM Deal" else "lead_owner"
		owner = frappe.db.get_value(*record, field)
		if owner and frappe.db.get_value("User", owner, "enabled"):
			return owner
	return user_for_phone(doc.sender_phone) or writeback.owner_of(doc.chat_id)


# --- Due date -----------------------------------------------------------------------------------------


def due_on(value: str | None):
	"""The given day at the end of the working day; no day: today, or tomorrow after hours."""
	end = frappe.db.get_single_value("Periskope Settings", "working_hours_end") or "19:00:00"
	try:
		day = getdate(value) if (value or "").strip() else None
	except Exception:
		day = None
	due = get_datetime(f"{day or getdate()} {end}")
	if not day and due <= now_datetime():
		due = add_days(due, 1)
	return due


# --- Creating -----------------------------------------------------------------------------------------


def _key_for(message: str, number: int) -> str:
	return f"{message}|chat-task|{number}"


def from_message(message: str, items: list | None) -> list[str]:
	"""Create the CRM Tasks the AI found in a group message. Returns the task names. Never raises."""
	try:
		if not items or not frappe.db.get_single_value("Periskope Settings", "create_chat_tasks"):
			return []
		doc = frappe.db.get_value("Periskope Message", message, MESSAGE_FIELDS, as_dict=True)
		if not doc or not doc.is_group:
			return []
		if frappe.db.exists("Periskope CRM Record", {"key": ["like", f"{message}|chat-task|%"]}):
			return []  # made once
		made = []
		for number, item in enumerate(items[:MAX_TASKS], start=1):
			name = _create(doc, number, item if isinstance(item, dict) else {})
			if name:
				made.append(name)
		return made
	except Exception:
		frappe.log_error(title=f"Periskope: could not create the tasks of message {message}")
		return []


def _create(doc, number: int, item: dict) -> str | None:
	title = writeback.rep_text(item.get("task"), 140)
	if not title:
		return None
	customer = (item.get("customer") or "").strip()[:140]
	# The assignee decides which of a customer's deals is meant, so find them first.
	first_guess = _assignee(item, doc, None)
	record, check = find_record(customer, first_guess)
	assignee = _assignee(item, doc, record)
	sender = user_for_phone(doc.sender_phone)
	sender_name = frappe.db.get_value("User", sender, "full_name") if sender else doc.sender_phone
	group = doc.chat_name or frappe.db.get_value("Periskope Group", {"chat_id": doc.chat_id}, "group_name")
	lines = [
		_("From {0} in {1}:").format(sender_name or _("a colleague"), group or _("a group chat")),
		writeback.rep_text(doc.body, 1500),
	]
	if customer and not record:
		lines.insert(0, _("Customer: {0}").format(customer))
	if check:
		lines.insert(0, check)
	description = "\n".join(line for line in lines if line)

	def write():
		task = insert_as_system(
			{
				"doctype": "CRM Task",
				"title": title,
				"description": writeback._html(description),
				"priority": "High" if doc.severity == "High" else "Medium",
				"status": "Todo",
				"due_date": due_on(item.get("due_date")),
				"assigned_to": assignee,
				"reference_doctype": record[0] if record else None,
				"reference_docname": record[1] if record else None,
			}
		)
		insert_as_system(
			{
				"doctype": "Periskope CRM Record",
				"key": _key_for(doc.name, number),
				"kind": "Task",
				"chat_id": doc.chat_id,
				"reference_doctype": record[0] if record else None,
				"reference_name": record[1] if record else None,
				"target_doctype": "CRM Task",
				"target_name": str(task.name),
			}
		)
		return str(task.name)

	return safety.crm_write(
		doc.chat_id,
		"Create Task",
		_("Created task: {0}").format(title) + (f" ({assignee})" if assignee else ""),
		write,
		target_doctype="CRM Task",
		reference_doctype=record[0] if record else None,
		reference_name=record[1] if record else None,
		detail={"message": doc.name, "customer": customer, "assignee": assignee},
	)
