"""Answer a customer on WhatsApp when no rep has, from the system's data.

1. A customer message in a 1-to-1 chat that needs a reply starts the clock: a Waiting row due
   "Reply When No Rep Answers Within" minutes after the first unanswered message. Later messages
   join it without moving it, so a customer who keeps writing is still answered in time.
2. A rep's reply cancels the row: a human got there first. There is no pause: the next unanswered
   message starts a new clock.
3. A job every minute handles the rows that are due, day or night: it reads all the unanswered
   messages, asks answer.py, and sends either the answer (confident, from data) or a holding reply
   the AI words for the situation (an apology for a complaint, thanks for an enquiry; the fixed
   holding message when the AI cannot be asked). In the holding case the rep also gets a task and
   the reminder ladder follows. After a holding reply only the messages that came later are
   answered, so a new question is not held back by an earlier complaint.

Nothing is sent unless "Reply to Customers" is on, and never in a group. In test mode (safety.py) a
chat that is not on the allow list gets a dry run: the reply is prepared and kept on the row, nothing
is sent.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from periskope_connector import answer, crm_context, health, reminders, safety, writeback
from periskope_connector.ai import analyse, assistant
from periskope_connector.outbound import send as send_message
from periskope_connector.setup_status import auto_reply_on
from periskope_connector.system import insert_as_system
from periskope_connector.text_rules import readable_text

FRESH_MINUTES = 30  # a message older than this (recovered by the backfill) is not answered
DEFAULT_DELAY_MINUTES = 15
MIN_CONFIDENCE = 80  # below this the customer gets the holding message and the rep a task
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
	"transcript",
	"is_deleted",
]
# What a sent row makes of its WhatsApp copy (Periskope Message.agent_reply).
AGENT_REPLY = {"Sent": "Answer", "Holding Sent": "Holding", "Welcome Sent": "Welcome"}
DEFAULT_HOLDING = "Thanks for reaching out! We've noted your request, and our sales representative will contact you shortly."


def _settings():
	return frappe.get_single("Periskope Settings")


def delay_for(settings) -> timedelta:
	"""How long a rep has to answer before the AI does."""
	minutes = settings.reply_delay_minutes
	return timedelta(minutes=max(float(DEFAULT_DELAY_MINUTES if minutes is None else minutes), 0))


def on_message(doc) -> str:
	"""Called after every saved message. Returns what happened (for tests). Never raises."""
	try:
		if doc.direction == "Outgoing":
			if doc.agent_reply:
				return "own"
			# A human answered in time: the AI does not reply to what the rep has answered.
			return "cancelled" if cancel(doc.chat_id, _("A rep replied")) else ""
		from periskope_connector.chat import ai_off

		if ai_off(doc.chat_id):
			return ""
		return schedule(doc)
	except Exception:
		frappe.log_error(title="Periskope: could not schedule the auto reply")
		return "error"


def schedule(doc) -> str:
	settings = _settings()
	if not auto_reply_on(settings) or doc.is_group:
		return ""  # never in a group: the team talks there
	if not readable_text(doc) or not reminders.needs_reply(doc):
		return ""  # nothing to answer yet (a voice note is scheduled once it is transcribed)
	if doc.message_timestamp and get_datetime(doc.message_timestamp) < now_datetime() - timedelta(
		minutes=FRESH_MINUTES
	):
		return ""
	status = frappe.db.get_value("Periskope Contact", {"phone_key": doc.customer_phone}, "status")
	if doc.customer_phone and (status or "").startswith("Ignored"):
		return ""
	name = frappe.db.get_value("Periskope Auto Reply", {"chat_id": doc.chat_id, "status": "Waiting"})
	values = {"last_customer_message": doc.name, "rep": doc.rep, "org_phone": doc.org_phone}
	if name:
		frappe.db.set_value("Periskope Auto Reply", name, values)
		return "joined"
	insert_as_system(
		{
			"doctype": "Periskope Auto Reply",
			"chat_id": doc.chat_id,
			"contact_number": doc.contact_number,
			"status": "Waiting",
			"due_on": now_datetime() + delay_for(settings),
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
	"""For a message from our own number: "Answer", "Holding" or "Welcome" when it is what the
	assistant just sent, so it is not taken for a rep's reply. "" for a human's message."""
	since = now_datetime() - timedelta(minutes=MATCH_MINUTES)
	row = frappe.db.get_value(
		"Periskope Auto Reply",
		{
			"chat_id": chat_id,
			"status": ["in", list(AGENT_REPLY)],
			"sent_on": [">=", since],
			"reply_text": (body or "").strip(),
		},
		["name", "status"],
		as_dict=True,
	)
	if not row:
		return ""
	return AGENT_REPLY[row.status]


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
	if not auto_reply_on():
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


def _guarded(title: str, fn):
	"""Run fn; on an error undo only its own writes and log it. Used for the work that follows a
	send: the customer already has the message, so the row must still record it."""
	frappe.db.savepoint("periskope_auto_reply_step")
	try:
		return fn()
	except Exception:
		frappe.db.rollback(save_point="periskope_auto_reply_step")
		health.log_once(title)
		return None


def _follow_up(row, pending: list, rep: str | None):
	"""The rep must pick this up: a reminder (so the SLA ladder follows), a task and a bell. On a
	chat with a lead or deal these are a CRM Task and a CRM notification, so the rep sees them in
	the CRM; otherwise a desk ToDo and notification. Never raises (for example a site's ToDo server
	script failing): returns None then."""
	return _guarded(
		"Periskope: could not create the auto reply follow-up", lambda: _open_follow_up(row, pending, rep)
	)


def _open_follow_up(row, pending: list, rep: str | None):
	cfg = reminders.get_config()
	now = now_datetime()
	reminder = reminders._open_reminder(row.chat_id) or reminders.create_reminder(
		row.chat_id, pending, rep, cfg, now, 0.0
	)
	if rep or crm_context.record_of(row.chat_id):
		reminders._create_todo(reminder, 0.0)
		reminders._bell(rep, pending[-1], 0.0, row.chat_id)
	return reminder.name


def _fail_and_follow(name: str, row, pending, rep, message: str):
	follow = _follow_up(row, pending, rep) if pending else None
	frappe.db.set_value(
		"Periskope Auto Reply", name, {"status": "Failed", "error": message[:500], "follow_up": follow}
	)


def _held_until(row, messages: list):
	"""Up to when the customer was told a rep will come back: our last holding reply's WhatsApp echo,
	or the last customer message that reply covered (the echo may not have arrived yet). Both are
	WhatsApp times, so they compare with the customer's messages."""
	times = [m.message_timestamp for m in messages if m.agent_reply == "Holding"]
	covered = frappe.db.get_value(
		"Periskope Auto Reply",
		{"chat_id": row.chat_id, "status": "Holding Sent", "name": ["!=", row.name]},
		"last_customer_message",
		order_by="sent_on desc",
	)
	stamp = covered and frappe.db.get_value("Periskope Message", covered, "message_timestamp")
	if stamp:
		times.append(get_datetime(stamp))
	return max(times, default=None)


def _cancel_siblings(row) -> int:
	"""Cancel the other Waiting rows of this row's chat."""
	names = frappe.get_all(
		"Periskope Auto Reply",
		filters={"chat_id": row.chat_id, "status": "Waiting", "name": ["!=", row.name]},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(
			"Periskope Auto Reply",
			name,
			{"status": "Cancelled", "cancel_reason": _("Answered together with {0}").format(row.name)},
		)
	return len(names)


def _already_sent(row, last_pending) -> bool:
	"""Whether another row of this chat already sent a reply (an answer or the holding message) that
	covered the customer's latest waiting message. Its WhatsApp copy may not have arrived yet, so
	the messages alone do not show it."""
	covered = frappe.get_all(
		"Periskope Auto Reply",
		filters={
			"chat_id": row.chat_id,
			"status": ["in", ["Sent", "Holding Sent"]],
			"name": ["!=", row.name],
			"last_customer_message": ["is", "set"],
		},
		pluck="last_customer_message",
		order_by="sent_on desc",
		limit=1,
	)
	if not covered:
		return False
	if covered[0] == last_pending.name:
		return True
	stamp = frappe.db.get_value("Periskope Message", covered[0], "message_timestamp")
	return bool(
		stamp and last_pending.message_timestamp and get_datetime(stamp) >= last_pending.message_timestamp
	)


def process(name: str) -> str:
	"""Handle one due row. Returns the final status."""
	settings = _settings()
	row = frappe.get_doc("Periskope Auto Reply", name)
	if row.status != "Waiting":
		return row.status

	cfg = reminders.get_config(settings)

	def end(status, reason=""):
		frappe.db.set_value("Periskope Auto Reply", name, {"status": status, "cancel_reason": reason})
		return status

	if not auto_reply_on(settings):
		return end("Cancelled", _("Auto reply was switched off"))
	from periskope_connector.chat import ai_off

	if ai_off(row.chat_id):
		return end("Cancelled", _("AI is off for this chat"))

	messages = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": row.chat_id},
		fields=MESSAGE_FIELDS,
		order_by="message_timestamp desc",
		limit=40,
	)
	messages.reverse()
	pending = reminders.unanswered(messages, cfg.ack_words)
	held = _held_until(row, messages)
	held_before = bool(held and pending and held >= pending[0].message_timestamp)
	if held_before:
		pending = [m for m in pending if m.message_timestamp > held]
	if not pending:
		return end("Cancelled", _("Already answered, or nothing to answer"))
	# Two messages saved at the same moment can each have started a clock (schedule): this row
	# answers for all of them, and never repeats what another row has already sent.
	_cancel_siblings(row)
	if _already_sent(row, pending[-1]):
		return end("Cancelled", _("The assistant already replied to this message"))

	customer_text = "\n".join(readable_text(m) for m in pending if readable_text(m))
	rep = next((m.rep for m in reversed(messages) if m.rep), None) or row.rep
	phone = row.org_phone or pending[-1].org_phone
	# The last message this reply covers: a later holding reply starts after it (_held_until).
	frappe.db.set_value(
		"Periskope Auto Reply",
		name,
		{"customer_text": customer_text, "last_customer_message": pending[-1].name},
	)

	result = {
		"can_answer": False,
		"answer": "",
		"confidence": 0,
		"used_sources": [],
		"sources": [],
		"holding": "",
	}
	ready, reason = assistant.readiness()
	if not ready:
		# No AI call at all: the customer gets the holding message, the rep a task, the admin one email.
		health.notify_admin_once(reason)
	else:
		try:
			result = answer.build(
				customer_text, row.contact_number or "", bool(pending[-1].is_group), chat_id=row.chat_id
			)
		except Exception:
			health.log_once("Periskope: could not prepare an auto reply")

	confident = result["can_answer"] and result["confidence"] >= MIN_CONFIDENCE
	if not confident and held_before:
		# The customer was already told a rep will come back; do not repeat it.
		return end("Skipped", _("The holding message was already sent"))
	text = result["answer"] if confident else (result.get("holding") or DEFAULT_HOLDING)
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

	# Record the reply before it goes out, and commit: WhatsApp's copy of our own message can arrive
	# through the webhook within a second, and it is told from a rep's reply by this row (match_sent).
	frappe.db.set_value(
		"Periskope Auto Reply",
		name,
		{
			"status": status,
			"reply_text": text,
			"confidence": result["confidence"],
			"sources": ", ".join(result["used_sources"]),
			"sent_on": now_datetime(),
		},
	)
	frappe.db.commit()
	try:
		send_message(row.chat_id, text, phone, origin="Auto Reply", detail={"auto_reply": name})
	except safety.SendNotAllowed as e:
		# Test mode: keep what would have been sent; nobody is told, nothing is followed up.
		frappe.db.set_value(
			"Periskope Auto Reply",
			name,
			{
				"status": e.mode,
				"sent_on": None,
				"cancel_reason": _("Holding message") if not confident else "",
			},
		)
		return e.mode
	except Exception as e:
		frappe.db.set_value("Periskope Auto Reply", name, "sent_on", None)
		_fail_and_follow(name, row, pending, rep, str(e))
		return "Failed"

	# The message is out (and its send log committed for the echo); the rep's follow-up and the CRM
	# updates come after, and none of them can turn the row into Failed.
	frappe.db.commit()
	if not confident:
		frappe.db.set_value("Periskope Auto Reply", name, "follow_up", _follow_up(row, pending, rep))
	_guarded(
		"Periskope: could not update the CRM after an auto reply",
		lambda: _after_send(row, text, confident, customer_text, result["used_sources"]),
	)
	return status


def _after_send(row, text: str, confident: bool, customer_text: str, used_sources: list):
	# What the rep sees on the lead or deal: the reply (or that a rep must answer) on the timeline,
	# and soon after an updated summary, notes and follow-up (ai/analyse.py).
	if confident:
		writeback.timeline(row.chat_id, _("AI replied on WhatsApp: {0}").format(text))
	else:
		writeback.timeline(
			row.chat_id,
			_(
				"AI could not answer from the data and told the customer a rep will reply. Customer asked: {0}"
			).format(customer_text[:500]),
		)
	analyse.queue(row.chat_id)
	for source in used_sources:
		if source.startswith("KB:"):
			frappe.db.set_value(
				"Periskope Knowledge Entry", source[3:], "last_used", now_datetime(), update_modified=False
			)
