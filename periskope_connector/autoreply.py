"""Answer customers on WhatsApp, after a delay, from the system's data.

1. A customer message (1-to-1, text, needs a reply) makes or moves a Waiting row for the chat to
   `delay + a random extra`. A newer message moves it again, so the reply reads everything the
   customer wrote.
2. A message from a rep cancels the row: a human got there first.
3. A job every minute handles the rows that are due: it reads all the unanswered messages, asks
   answer.py, and sends either the answer (confident, from data) or the holding message. In the
   holding case it also gives the rep a ToDo and opens a reply reminder, so the SLA ladder follows.

Nothing is sent unless "Enable Auto Reply" is on. In test mode (safety.py) a chat that is not on
the allow list gets a dry run: the reply is prepared and kept on the row, nothing is sent.
"""

import random
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime, today

from periskope_connector import answer, chat, reminders, safety
from periskope_connector.outbound import send as send_message
from periskope_connector.setup_status import feature_on
from periskope_connector.system import insert_as_system

FRESH_MINUTES = 30  # a message older than this (recovered by the backfill) is not answered
MATCH_MINUTES = 30
MESSAGE_FIELDS = [
	"name",
	"chat_id",
	"direction",
	"body",
	"message_type",
	"message_timestamp",
	"rep",
	"contact_number",
	"is_group",
	"classification_status",
	"needs_reply",
	"severity",
	"org_phone",
	"agent_reply",
]
DEFAULT_HOLDING = "Thanks for reaching out! We've noted your request, and our sales representative will contact you shortly."


def _settings():
	return frappe.get_single("Periskope Settings")


def delay_for(settings, rng=random) -> timedelta:
	"""The configured delay plus up to `jitter` percent more."""
	minutes = float(settings.reply_delay_minutes if settings.reply_delay_minutes is not None else 3)
	extra = minutes * (int(settings.reply_delay_jitter_percent or 0) / 100) * rng.random()
	return timedelta(minutes=max(minutes, 0) + extra)


def on_message(doc) -> str:
	"""Called after every saved message. Returns what happened (for tests). Never raises."""
	try:
		if doc.direction == "Outgoing":
			if doc.agent_reply:
				return "own"
			# A human answered: the rep takes the chat over and the AI stays out of it for a while.
			chat.pause(doc.chat_id, doc.rep or "Administrator", "Rep replied")
			return "cancelled" if cancel(doc.chat_id, _("A rep replied")) else ""
		return schedule(doc)
	except Exception:
		frappe.log_error(title="Periskope: could not schedule the auto reply")
		return "error"


def schedule(doc) -> str:
	settings = _settings()
	if not feature_on("enable_auto_reply", settings):
		return ""
	if doc.is_group and not settings.reply_group_chats:
		return ""
	if chat.is_paused(doc.chat_id):
		return ""
	if doc.message_type != "text" or not reminders.needs_reply(doc):
		return ""
	if doc.message_timestamp and get_datetime(doc.message_timestamp) < now_datetime() - timedelta(
		minutes=FRESH_MINUTES
	):
		return ""
	status = frappe.db.get_value("Periskope Contact", {"phone_key": doc.customer_phone}, "status")
	if doc.customer_phone and (status or "").startswith("Ignored"):
		return ""
	due = now_datetime() + delay_for(settings)
	name = frappe.db.get_value("Periskope Auto Reply", {"chat_id": doc.chat_id, "status": "Waiting"})
	values = {"due_on": due, "last_customer_message": doc.name, "rep": doc.rep, "org_phone": doc.org_phone}
	if name:
		frappe.db.set_value("Periskope Auto Reply", name, values)
		return "moved"
	insert_as_system(
		{
			"doctype": "Periskope Auto Reply",
			"chat_id": doc.chat_id,
			"contact_number": doc.contact_number,
			"status": "Waiting",
			**values,
		}
	)
	return "scheduled"


def cancel(chat_id: str, reason: str) -> int:
	rows = frappe.get_all(
		"Periskope Auto Reply", filters={"chat_id": chat_id, "status": "Waiting"}, pluck="name"
	)
	for name in rows:
		frappe.db.set_value("Periskope Auto Reply", name, {"status": "Cancelled", "cancel_reason": reason})
	return len(rows)


def match_sent(chat_id: str, body: str) -> str:
	"""For a message from our own number: "Answer" or "Holding" when it is what the assistant just
	sent, so it is not taken for a rep's reply. "" for a human's message."""
	since = now_datetime() - timedelta(minutes=MATCH_MINUTES)
	row = frappe.db.get_value(
		"Periskope Auto Reply",
		{
			"chat_id": chat_id,
			"status": ["in", ["Sent", "Holding Sent"]],
			"sent_on": [">=", since],
			"reply_text": (body or "").strip(),
		},
		["name", "status"],
		as_dict=True,
	)
	if not row:
		return ""
	return "Answer" if row.status == "Sent" else "Holding"


# --- Sending ------------------------------------------------------------------------------------


def due_rows() -> list[str]:
	return frappe.get_all(
		"Periskope Auto Reply",
		filters={"status": "Waiting", "due_on": ["<=", now_datetime()]},
		pluck="name",
		order_by="due_on asc",
		limit=20,
	)


def run_due() -> int:
	"""Scheduler, every minute. Returns how many rows were handled."""
	settings = _settings()
	if not feature_on("enable_auto_reply", settings):
		return 0
	handled = 0
	for name in due_rows():
		try:
			process(name)
			frappe.db.commit()
			handled += 1
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Periskope: auto reply {name} failed")
			frappe.db.set_value(
				"Periskope Auto Reply", name, {"status": "Failed", "error": "See the Error Log"}
			)
			frappe.db.commit()
	return handled


def _follow_up(row, pending: list, rep: str | None):
	"""The rep must pick this up: a reminder (so the SLA ladder follows) and a ToDo and a bell."""
	cfg = reminders.get_config()
	now = now_datetime()
	reminder = reminders._open_reminder(row.chat_id) or reminders.create_reminder(
		row.chat_id, pending, rep, cfg, now, 0.0
	)
	if rep:
		reminders._create_todo(reminder, 0.0)
		reminders._bell(rep, pending[-1], 0.0)
	return reminder.name


def _fail_and_follow(name: str, row, pending, rep, message: str):
	follow = _follow_up(row, pending, rep) if pending else None
	frappe.db.set_value(
		"Periskope Auto Reply", name, {"status": "Failed", "error": message[:500], "follow_up": follow}
	)


def process(name: str) -> str:
	"""Handle one due row. Returns the final status."""
	settings = _settings()
	row = frappe.get_doc("Periskope Auto Reply", name)
	if row.status != "Waiting":
		return row.status

	cfg = reminders.get_config(settings)
	if settings.reply_working_hours_only and not reminders.is_working_time(now_datetime(), cfg):
		return "Waiting"  # picked up again in the first working minute

	def end(status, reason=""):
		frappe.db.set_value("Periskope Auto Reply", name, {"status": status, "cancel_reason": reason})
		return status

	if not feature_on("enable_auto_reply", settings):
		return end("Cancelled", _("Auto reply was switched off"))
	if chat.is_paused(row.chat_id):
		return end("Cancelled", _("A rep took this chat over"))

	messages = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": row.chat_id},
		fields=MESSAGE_FIELDS,
		order_by="message_timestamp desc",
		limit=40,
	)
	messages.reverse()
	pending = reminders.unanswered(messages, cfg.ack_words)
	if not pending:
		return end("Cancelled", _("Already answered, or nothing to answer"))

	cap = int(settings.reply_max_per_chat_per_day or 0)
	if (
		cap
		and frappe.db.count(
			"Periskope Auto Reply",
			{"chat_id": row.chat_id, "status": ["in", ["Sent", "Holding Sent"]], "sent_on": [">=", today()]},
		)
		>= cap
	):
		pending_rep = next((m.rep for m in reversed(messages) if m.rep), None)
		frappe.db.set_value(
			"Periskope Auto Reply", name, {"follow_up": _follow_up(row, pending, pending_rep)}
		)
		return end("Skipped", _("Daily limit for this chat reached"))

	customer_text = "\n".join((m.body or "").strip() for m in pending if (m.body or "").strip())
	rep = next((m.rep for m in reversed(messages) if m.rep), None) or row.rep
	phone = row.org_phone or pending[-1].org_phone
	frappe.db.set_value("Periskope Auto Reply", name, "customer_text", customer_text)

	result = {"can_answer": False, "answer": "", "confidence": 0, "used_sources": [], "sources": []}
	try:
		result = answer.build(customer_text, row.contact_number or "", bool(pending[-1].is_group))
	except Exception:
		frappe.log_error(title=f"Periskope: could not prepare the answer for {row.chat_id}")

	min_conf = float(settings.reply_min_confidence or 80)
	confident = result["can_answer"] and result["confidence"] >= min_conf
	if not confident and frappe.db.exists(
		"Periskope Auto Reply",
		{"chat_id": row.chat_id, "status": "Holding Sent", "sent_on": [">=", pending[0].message_timestamp]},
	):
		# The customer was already told a rep will come back; do not repeat it.
		return end("Skipped", _("The holding message was already sent"))
	text = result["answer"] if confident else ((settings.holding_message or "").strip() or DEFAULT_HOLDING)
	status = "Sent" if confident else "Holding Sent"

	# A rep may have written while the answer was prepared.
	if frappe.db.exists(
		"Periskope Message",
		{
			"chat_id": row.chat_id,
			"direction": "Outgoing",
			"agent_reply": ["is", "not set"],
			"message_timestamp": [">", pending[-1].message_timestamp],
		},
	):
		return end("Cancelled", _("A rep replied"))

	try:
		send_message(row.chat_id, text, phone, origin="Auto Reply", detail={"auto_reply": name})
	except safety.SendNotAllowed as e:
		# Test mode: keep what would have been sent; nobody is told, nothing is followed up.
		frappe.db.set_value(
			"Periskope Auto Reply",
			name,
			{
				"status": e.mode,
				"reply_text": text,
				"confidence": result["confidence"],
				"sources": ", ".join(result["used_sources"]),
				"cancel_reason": _("Holding message") if not confident else "",
			},
		)
		return e.mode
	except Exception as e:
		_fail_and_follow(name, row, pending, rep, str(e))
		return "Failed"

	values = {
		"status": status,
		"reply_text": text,
		"confidence": result["confidence"],
		"sources": ", ".join(result["used_sources"]),
		"sent_on": now_datetime(),
	}
	if not confident:
		values["follow_up"] = _follow_up(row, pending, rep)
	frappe.db.set_value("Periskope Auto Reply", name, values)
	for source in result["used_sources"]:
		if source.startswith("KB:"):
			frappe.db.set_value(
				"Periskope Knowledge Entry", source[3:], "last_used", now_datetime(), update_modified=False
			)
	return status
