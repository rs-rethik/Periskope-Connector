"""What the agent writes into the CRM, so a rep sees everything on the lead or deal in /crm:

- one "WhatsApp summary" note per record (what the customer wants, what was answered, what is
  pending), kept up to date instead of repeated,
- one note per topic the customer told us about (requirements, quantities, delivery, ...),
- one open task per chat and reason (a holding message, a waiting customer, a tag that needs the
  rep), with a clear title, reason and due date, updated while it is open,
- a timeline comment for each AI reply,
- the record's status, but only for a clear step forward; Won, Lost and On Hold are proposed to the
  owner as a task, never set by the agent,
- the company (CRM Organization) of a customer or group.

Every write goes through safety.crm_write: in test mode only allowed chats change the CRM. The
Periskope CRM Record list remembers which note or task belongs to which chat and topic. All text a
rep sees passes rep_text(): no chat ids, internal ids, confidence figures or raw data.
"""

import hashlib
import re

import frappe
from frappe import _
from frappe.utils import add_to_date, escape_html, now_datetime

from periskope_connector import chat, crm_context, numbers, safety
from periskope_connector.system import insert_as_system, save_as_system

TOPICS = {
	"requirements": "Requirements",
	"quantities": "Quantities",
	"delivery": "Delivery",
	"payment_terms": "Payment terms",
	"contact_person": "Contact person",
	"address": "Address",
	"complaint": "Complaint",
	"other": "Other details",
}
SUMMARY_TITLE = "WhatsApp summary"
OPEN_TASK = ("Backlog", "Todo", "In Progress")
FORWARD_TYPES = ("Open", "Ongoing")
CONFIRM_TYPES = ("Won", "Lost", "On Hold")


# --- Rep-facing text ----------------------------------------------------------------------------

_INTERNAL = [
	re.compile(r"\b\d{6,}@(c|g)\.us\b"),  # chat ids
	re.compile(r"\b(KB|ITEM|ORDER|QUOTE|DEAL|CRM):[\w\-./]+"),  # source ids
	re.compile(r"\(?\bconfidence\b[^.,;)]*\)?", re.IGNORECASE),
	re.compile(r"\b(true|false)_\S+"),  # Periskope message ids
]


def rep_text(text: str | None, limit: int = 2000) -> str:
	"""Plain words for a rep: internal ids, confidence figures and JSON are removed."""
	text = str(text or "")
	if text.strip().startswith(("{", "[")):
		text = re.sub(r"[{}\[\]\"]", " ", text)
	for pattern in _INTERNAL:
		text = pattern.sub("", text)
	text = re.sub(r"[ \t]+", " ", text)
	text = re.sub(r"\s+([.,;:])", r"\1", text)
	return text.strip()[:limit]


def _html(text: str) -> str:
	return "".join(f"<p>{escape_html(line)}</p>" for line in rep_text(text).split("\n") if line.strip())


def _hash(*parts) -> str:
	return hashlib.sha1("\n".join(str(p) for p in parts).encode()).hexdigest()


# --- Who and where ------------------------------------------------------------------------------


def owner_of(chat_id: str, record: tuple[str, str] | None = None) -> str | None:
	"""The rep for this chat: the record's owner, else the rep of the company number, else the
	default sales rep. Only enabled users."""
	record = record or crm_context.record_of(chat_id)
	candidates = []
	if record:
		field = "deal_owner" if record[0] == "CRM Deal" else "lead_owner"
		candidates.append(frappe.db.get_value(record[0], record[1], field))
	org_phone = chat.number_of(chat_id)
	if org_phone:
		candidates.append(numbers.rep(org_phone))
	candidates.append(frappe.db.get_single_value("Periskope Settings", "default_rep"))
	return next(
		(u for u in candidates if u and u != "Administrator" and frappe.db.get_value("User", u, "enabled")),
		None,
	)


def _tracked(key: str) -> frappe._dict | None:
	return frappe.db.get_value(
		"Periskope CRM Record",
		{"key": key},
		["name", "target_doctype", "target_name", "content_hash"],
		as_dict=True,
	)


def _track(key, kind, chat_id, record, target_doctype, target_name, content_hash):
	row = _tracked(key)
	values = {
		"kind": kind,
		"chat_id": chat_id,
		"reference_doctype": record[0],
		"reference_name": record[1],
		"target_doctype": target_doctype,
		"target_name": target_name,
		"content_hash": content_hash,
	}
	if row:
		frappe.db.set_value("Periskope CRM Record", row.name, values, update_modified=False)
	else:
		insert_as_system({"doctype": "Periskope CRM Record", "key": key, **values})


def _ref(record) -> dict:
	return {"reference_doctype": record[0], "reference_name": record[1]}


# --- Notes --------------------------------------------------------------------------------------


def _upsert_note(chat_id: str, key: str, kind: str, title: str, content_html: str) -> str | None:
	record = crm_context.record_of(chat_id)
	if not record:
		return None
	digest = _hash(title, content_html)
	row = _tracked(key)
	if row and row.content_hash == digest and frappe.db.exists("FCRM Note", row.target_name):
		return row.target_name

	def write():
		if row and frappe.db.exists("FCRM Note", row.target_name):
			note = frappe.get_doc("FCRM Note", row.target_name)
			note.title, note.content = title, content_html
			save_as_system(note)
		else:
			note = insert_as_system(
				{
					"doctype": "FCRM Note",
					"title": title,
					"content": content_html,
					"reference_doctype": record[0],
					"reference_docname": record[1],
				}
			)
		_track(key, kind, chat_id, record, "FCRM Note", note.name, digest)
		return note.name

	action = "Update Note" if row else "Create Note"
	return safety.crm_write(
		chat_id,
		action,
		f"{action.split()[0]}d note: {title}",
		write,
		target_doctype="FCRM Note",
		**_ref(record),
	)


def summary(chat_id: str, wants: str, answered: str, pending: str) -> str | None:
	"""The one note a rep reads first: what the customer wants, what was answered, what is pending."""
	parts = [
		(_("What the customer wants"), wants),
		(_("What we answered"), answered),
		(_("Still pending"), pending),
	]
	content = "".join(
		f"<p><b>{escape_html(label)}</b></p>{_html(text) or '<p>-</p>'}" for label, text in parts
	)
	name = _upsert_note(chat_id, f"{chat_id}|summary", "Summary", _(SUMMARY_TITLE), content)
	if name:
		chat.set_on_all(chat_id, {"summary_note": name})
	return name


def upsert_note(chat_id: str, topic: str, text: str) -> str | None:
	"""A note for one topic the customer told us about, written as plain sentences."""
	topic = topic if topic in TOPICS else "other"
	text = rep_text(text, 1500)
	if not text:
		return None
	return _upsert_note(chat_id, f"{chat_id}|note|{topic}", "Note", _(TOPICS[topic]), _html(text))


# --- Tasks and notifications --------------------------------------------------------------------


def upsert_task(
	chat_id: str,
	reason: str,
	title: str,
	description: str = "",
	due=None,
	priority: str = "Medium",
	assignee: str | None = None,
) -> str | None:
	"""One open CRM Task per chat and reason; while it is open it is updated, not repeated. Returns
	the task name, or None when the chat has no CRM record or test mode keeps it a dry run."""
	record = crm_context.record_of(chat_id)
	if not record:
		return None
	key = f"{chat_id}|task|{reason}"
	title = rep_text(title, 140)
	description = rep_text(description, 1500)
	due = due or add_to_date(now_datetime(), hours=4)
	assignee = assignee or owner_of(chat_id, record)
	row = _tracked(key)
	open_task = (
		row
		and frappe.db.exists("CRM Task", row.target_name)
		and frappe.db.get_value("CRM Task", row.target_name, "status") in OPEN_TASK
	)
	digest = _hash(title, description, priority)
	if open_task and row.content_hash == digest:
		return row.target_name

	def write():
		if open_task:
			task = frappe.get_doc("CRM Task", row.target_name)
			task.update({"title": title, "description": _html(description), "priority": priority})
			save_as_system(task)
		else:
			task = insert_as_system(
				{
					"doctype": "CRM Task",
					"title": title,
					"description": _html(description),
					"priority": priority,
					"status": "Todo",
					"due_date": due,
					"assigned_to": assignee,
					"reference_doctype": record[0],
					"reference_docname": record[1],
				}
			)
		_track(key, "Task", chat_id, record, "CRM Task", task.name, digest)
		return str(task.name)

	action = "Update Task" if open_task else "Create Task"
	return safety.crm_write(
		chat_id,
		action,
		f"{action.split()[0]}d task: {title}",
		write,
		target_doctype="CRM Task",
		**_ref(record),
	)


def notify(chat_id: str, text: str, kind: str = "Task", user: str | None = None) -> bool:
	"""A CRM bell notification for the rep, pointing at the lead or deal."""
	record = crm_context.record_of(chat_id)
	user = user or owner_of(chat_id, record)
	if not record or not user:
		return False
	try:
		from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
	except ImportError:
		return False
	message = rep_text(text, 300)

	def write():
		notify_user(
			{
				"owner": "Administrator",
				"assigned_to": user,
				"notification_type": kind,
				"message": message,
				"notification_text": f"<div class='mb-2 leading-5 text-ink-gray-5'><span>{escape_html(message)}</span></div>",
				"reference_doctype": record[0],
				"reference_docname": record[1],
				"redirect_to_doctype": record[0],
				"redirect_to_docname": record[1],
			}
		)
		return user

	return bool(
		safety.crm_write(chat_id, "Notification", f"Told {user}: {message[:80]}", write, **_ref(record))
	)


# --- Timeline, status, organization -------------------------------------------------------------


def timeline(chat_id: str, text: str) -> str | None:
	"""A comment on the lead or deal (shown in its Activity), e.g. an AI reply and its key points."""
	record = crm_context.record_of(chat_id)
	text = rep_text(text, 2000)
	if not record or not text:
		return None

	def write():
		comment = insert_as_system(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": record[0],
				"reference_name": record[1],
				"comment_email": "Administrator",
				"comment_by": _("AI assistant"),
				"content": _html(text),
			}
		)
		return comment.name

	return safety.crm_write(
		chat_id, "Timeline", f"Timeline: {text[:80]}", write, target_doctype="Comment", **_ref(record)
	)


def _status_info(doctype: str, status: str) -> frappe._dict | None:
	status_doctype = "CRM Deal Status" if doctype == "CRM Deal" else "CRM Lead Status"
	return frappe.db.get_value(status_doctype, status, ["name", "type", "position"], as_dict=True)


def set_status(chat_id: str, proposed: str, reason: str) -> str:
	"""Move the record forward when the conversation clearly shows it. Returns "Applied",
	"Proposed" (asked the owner to confirm), or "" (nothing to do)."""
	record = crm_context.record_of(chat_id)
	if not record or not proposed:
		return ""
	current = frappe.db.get_value(record[0], record[1], "status")
	target = _status_info(record[0], proposed)
	if not target or target.name == current:
		return ""
	now = _status_info(record[0], current) if current else None
	reason = rep_text(reason, 300)
	if target.type in CONFIRM_TYPES:
		task = upsert_task(
			chat_id,
			"status",
			_("Confirm: mark as {0}?").format(target.name),
			_("The WhatsApp conversation suggests this {0} is {1}: {2}").format(
				_("deal") if record[0] == "CRM Deal" else _("lead"), target.name, reason
			),
			priority="High",
		)
		safety.log(
			"Status Proposal",
			safety.crm_mode(chat_id),
			f"Proposed {target.name}",
			chat_id=chat_id,
			**_ref(record),
		)
		return "Proposed" if task or safety.crm_mode(chat_id) != safety.LIVE else ""
	forward = (
		target.type in FORWARD_TYPES
		and (not now or now.type in FORWARD_TYPES)
		and (target.position or 0) > ((now.position or 0) if now else -1)
	)
	if not forward:
		return ""

	def write():
		doc = frappe.get_doc(*record)
		doc.status = target.name
		save_as_system(doc)
		return doc.name

	done = safety.crm_write(
		chat_id,
		"Status Change",
		f"Status {current} → {target.name}: {reason}",
		write,
		target_doctype=record[0],
		**_ref(record),
	)
	if done:
		timeline(chat_id, _("AI moved the status from {0} to {1}: {2}").format(current, target.name, reason))
	return "Applied"


def link_organization(chat_id: str, organization: str) -> str | None:
	"""Link the chat's customer (and its group) to a CRM Organization, made when it does not exist.
	Only fills an empty organization; a value set by a person is never replaced."""
	organization = rep_text(organization, 140).strip()
	if len(organization) < 3:
		return None
	record = crm_context.record_of(chat_id)
	existing = frappe.db.get_value("CRM Organization", {"organization_name": organization})

	def write():
		name = (
			existing
			or insert_as_system({"doctype": "CRM Organization", "organization_name": organization}).name
		)
		if record and not frappe.db.get_value(record[0], record[1], "organization"):
			frappe.db.set_value(record[0], record[1], "organization", name)
		if safety.is_group(chat_id) and frappe.db.exists("Periskope Group", {"chat_id": chat_id}):
			group = frappe.db.get_value("Periskope Group", {"chat_id": chat_id})
			if not frappe.db.get_value("Periskope Group", group, "organization"):
				frappe.db.set_value("Periskope Group", group, "organization", name)
		chat.set_on_all(chat_id, {"organization": name})
		return name

	return safety.crm_write(
		chat_id,
		"Link Organization",
		f"Linked to organization {organization}",
		write,
		target_doctype="CRM Organization",
		**(_ref(record) if record else {}),
	)


def on_task_update(doc, method=None):
	"""CRM Task doc_event: when the rep marks the reply task done, the customer is no longer waiting
	on the reminder ladder."""
	if doc.status not in ("Done", "Canceled") or not doc.has_value_changed("status"):
		return
	key = frappe.db.get_value(
		"Periskope CRM Record", {"target_doctype": "CRM Task", "target_name": str(doc.name)}, "key"
	)
	if not key or not key.endswith("|task|reply"):
		return
	from periskope_connector import reminders

	reminder = reminders._open_reminder(key.split("|", 1)[0])
	if reminder:
		reminders.close(reminder, now_datetime())
