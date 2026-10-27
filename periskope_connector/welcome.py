"""Welcome a new number and fill its lead from what the customer tells us.

1. The first message from a number the CRM does not know (1-to-1, not on a lead, deal or Frappe
   Contact, not ignored, never seen before on any company number) is answered at once: the AI writes
   a short welcome in the customer's language that thanks them and asks for their name, company,
   city or address and email, leaving out what they already said. The fixed DEFAULT_WELCOME goes
   when the AI cannot be asked.
2. The welcome is not an answer (text_rules.NOT_ANSWERS): the customer's question still waits for a
   rep, and the auto reply and the reply reminders run as usual.
3. Each later customer message in that chat (for CAPTURE_DAYS) has the chat read for those details. The
   customer's first reply to the welcome creates the lead when there is none yet, even when the AI
   cannot be asked (the details are then read by from_text's simple rules). Only empty fields are
   filled: name (while it is still the phone number or the name the app set), email and organization;
   the city or address becomes a note on the lead.

Nothing is sent unless "Reply to Customers" and "Welcome New Numbers" are on, and test mode applies
to the send and to every CRM write (safety.py). The welcome is a Periskope Auto Reply row of kind
Welcome, so it is logged with the other replies and its WhatsApp copy is told from a rep's message.
"""

import json
import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime, validate_email_address

from periskope_connector import crm_context, leads, safety, writeback
from periskope_connector.ai import assistant
from periskope_connector.answer import _no_new_numbers
from periskope_connector.autoreply import FRESH_MINUTES
from periskope_connector.contacts import is_ignored
from periskope_connector.linking import by_mobile, phone_e164
from periskope_connector.outbound import send as send_message
from periskope_connector.setup_status import auto_reply_on
from periskope_connector.system import insert_as_system
from periskope_connector.text_rules import readable_text

CAPTURE_DAYS = 7  # how long after the welcome the customer's messages are read for their details
MAX_MESSAGES = 20
DETAILS = ("name", "company", "city_address", "email")
LABELS = {"name": "name", "company": "company name", "city_address": "city or address", "email": "email"}
# Rows whose customer was (or in test mode would have been) welcomed; their replies are read.
WELCOMED = ("Welcome Sent", "Dry Run")
DEFAULT_WELCOME = (
	"Hi! Thanks for contacting us. To help you better, please share your name, company name, "
	"city or address, and email."
)

WELCOME_PROMPT = """You write the first WhatsApp reply to someone who has just messaged a company's sales number for the first time. It has two parts.
- greeting: greet them (by name if given) and thank them for contacting us. If they asked for something, add that our team will get back to them about it. Never answer it and never say they will get or receive anything: no facts, prices, stock, samples, dates, numbers or promises.
- request: one sentence politely asking them to share exactly the details listed under ASK FOR, all of them and nothing else (we already have their phone number). Empty only when ASK FOR says nothing.
Write both entirely in the customer's language and script (English when they wrote none), friendly and short, plain text, no markdown, no signature."""

WELCOME_SCHEMA = {
	"type": "object",
	"properties": {"greeting": {"type": "string"}, "request": {"type": "string"}},
	"required": ["greeting", "request"],
	"additionalProperties": False,
}

CAPTURE_PROMPT = """From a customer's WhatsApp messages to a company, pick out what they say about themselves. The company's own welcome message, when shown first, is only context; take nothing from it.
- name: the customer's own personal name.
- company: the name of their company, shop or business.
- city_address: their city or address, as they wrote it.
- email: their email address.
Only what the customer clearly states about themselves; an empty string for anything they did not give. Never guess."""

CAPTURE_SCHEMA = {
	"type": "object",
	"properties": {key: {"type": "string"} for key in DETAILS},
	"required": list(DETAILS),
	"additionalProperties": False,
}

MESSAGE_FIELDS = [
	"name",
	"contact_name",
	"chat_id",
	"direction",
	"is_group",
	"contact_number",
	"customer_phone",
	"org_phone",
	"rep",
	"message_timestamp",
	"body",
	"message_type",
	"transcript",
	"is_deleted",
]


def _settings():
	return frappe.get_single("Periskope Settings")


def is_on(settings=None) -> bool:
	settings = settings or _settings()
	return auto_reply_on(settings) and bool(settings.welcome_new_numbers)


def on_message(doc) -> str:
	"""Called after every saved message. Returns what was queued (for tests). Never raises."""
	try:
		if doc.direction != "Incoming" or doc.is_group or not doc.contact_number:
			return ""
		if welcome_row(doc.chat_id):
			if _capturing(doc.chat_id):
				_enqueue("capture", f"periskope_welcome_capture::{doc.chat_id}", chat_id=doc.chat_id)
				return "capture"
			return ""
		if is_new_number(doc):
			_enqueue("send", f"periskope_welcome::{doc.chat_id}", message=doc.name)
			return "welcome"
		return ""
	except Exception:
		frappe.log_error(title="Periskope: could not check for a welcome")
		return "error"


def _enqueue(method: str, job_id: str, **kwargs):
	frappe.enqueue(
		f"periskope_connector.welcome.{method}",
		queue="short",
		job_id=job_id,
		deduplicate=True,
		enqueue_after_commit=True,
		**kwargs,
	)


def is_new_number(doc) -> bool:
	"""The first message ever from a number nobody knows, while welcomes are on."""
	if not is_on() or not _unknown(doc.contact_number):
		return False
	if doc.message_timestamp and get_datetime(doc.message_timestamp) < now_datetime() - timedelta(
		minutes=FRESH_MINUTES
	):
		return False  # an old message the back-fill recovered
	# Never seen before on any company number, in either direction (a rep may have written first).
	return not frappe.db.exists(
		"Periskope Message", {"contact_number": doc.contact_number, "is_group": 0, "name": ["!=", doc.name]}
	)


def _unknown(number: str) -> bool:
	"""Not on a lead, deal or Frappe Contact, not ignored, and never welcomed."""
	if by_mobile(number) or is_ignored(number) or leads._known_contact(phone_e164(number)):
		return False
	return not frappe.db.exists("Periskope Auto Reply", {"contact_number": number, "kind": "Welcome"})


def welcome_row(chat_id: str) -> frappe._dict | None:
	return frappe.db.get_value(
		"Periskope Auto Reply",
		{"chat_id": chat_id, "kind": "Welcome", "status": ["in", WELCOMED]},
		["name", "creation", "captured", "reply_text"],
		as_dict=True,
		order_by="creation desc",
	)


def _captured(row) -> dict:
	try:
		return json.loads(row.captured) if isinstance(row.captured, str) else (row.captured or {})
	except ValueError:
		return {}


def _capturing(chat_id: str) -> bool:
	"""Whether the customer's messages are still read for their details."""
	row = welcome_row(chat_id)
	if not row or get_datetime(row.creation) < add_days(now_datetime(), -CAPTURE_DAYS):
		return False
	got = _captured(row)
	return not all(got.get(key) for key in DETAILS)


# --- The welcome ------------------------------------------------------------------------------------


def _customer_text(chat_id: str) -> tuple[str, list]:
	"""The customer's latest messages in the chat as one text, and the rows, oldest first."""
	rows = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": chat_id, "direction": "Incoming", "is_deleted": 0},
		fields=MESSAGE_FIELDS,
		order_by="message_timestamp desc",
		limit=MAX_MESSAGES,
	)
	rows.reverse()
	return "\n".join(t for t in (readable_text(m) for m in rows) if t), rows


def compose(customer_text: str) -> str:
	"""The AI's welcome for what the customer wrote, or DEFAULT_WELCOME when it cannot be asked or its
	reply fails the checks (no digits the customer did not write). What the customer already said
	about themselves is read first, so the welcome asks for exactly the rest."""
	if not assistant.is_ready():
		return DEFAULT_WELCOME
	try:
		known = extract("", customer_text) if customer_text else {}
		missing = [LABELS[key] for key in DETAILS if not known.get(key)]
		reply = assistant.complete_json(
			WELCOME_PROMPT,
			f"CUSTOMER MESSAGES:\n{customer_text or '(no text)'}\n\n"
			f"CUSTOMER NAME: {known.get('name') or 'not given'}\n"
			f"ASK FOR: {', '.join(missing) or 'nothing, they gave every detail'}",
			WELCOME_SCHEMA,
			400,
		)
	except Exception:
		frappe.log_error(title="Periskope: could not write the welcome")
		return DEFAULT_WELCOME
	greeting = (reply.get("greeting") or "").strip()
	request = (reply.get("request") or "").strip()
	text = f"{greeting} {request}".strip()
	if not greeting or (missing and not request) or len(text) > 1000:
		return DEFAULT_WELCOME
	return text if _no_new_numbers(text, customer_text) else DEFAULT_WELCOME


def send(message: str) -> str:
	"""Welcome the number of `message` (a background job). Returns the row's status, or "" when the
	number is no longer new or welcomes were switched off."""
	doc = frappe.db.get_value("Periskope Message", message, [*MESSAGE_FIELDS, "creation"], as_dict=True)
	if not doc or not is_on() or not _unknown(doc.contact_number):
		return ""
	# Still the number's first message (the customer may have written more since; those are read
	# below), and nobody has written to them yet.
	if frappe.db.exists(
		"Periskope Message",
		{"contact_number": doc.contact_number, "is_group": 0, "creation": ["<", doc.creation]},
	) or frappe.db.exists("Periskope Message", {"chat_id": doc.chat_id, "direction": "Outgoing"}):
		return ""
	customer_text, rows = _customer_text(doc.chat_id)
	text = compose(customer_text)

	# Record the welcome before it goes out, and commit: its WhatsApp copy can arrive within a second
	# and is told from a rep's message by this row (outbound.match_sent).
	row = insert_as_system(
		{
			"doctype": "Periskope Auto Reply",
			"kind": "Welcome",
			"chat_id": doc.chat_id,
			"contact_number": doc.contact_number,
			"org_phone": doc.org_phone,
			"rep": doc.rep,
			"status": "Welcome Sent",
			"last_customer_message": rows[-1].name if rows else doc.name,
			"customer_text": customer_text,
			"reply_text": text,
			"sent_on": now_datetime(),
		}
	)
	frappe.db.commit()

	def update(values):
		frappe.db.set_value("Periskope Auto Reply", row.name, values)
		frappe.db.commit()

	try:
		send_message(doc.chat_id, text, doc.org_phone, origin="Welcome", detail={"auto_reply": row.name})
	except safety.SendNotAllowed as e:
		# Test mode: the welcome is kept on the row; the customer's answers are still read.
		update({"status": e.mode, "sent_on": None, "cancel_reason": _("Welcome message")})
		return e.mode
	except Exception as e:
		update({"status": "Failed", "sent_on": None, "error": str(e)[:1000]})
		frappe.log_error(title="Periskope: could not send the welcome")
		return "Failed"
	frappe.db.commit()
	return "Welcome Sent"


# --- The customer's details -----------------------------------------------------------------------


def extract(welcome_text: str, customer_text: str) -> dict:
	"""{name, company, city_address, email} the customer gave, "" for what they did not."""
	welcome_part = f"COMPANY WELCOME:\n{welcome_text}\n\n" if welcome_text else ""
	reply = assistant.complete_json(
		CAPTURE_PROMPT,
		f"{welcome_part}CUSTOMER MESSAGES:\n{customer_text}",
		CAPTURE_SCHEMA,
		300,
	)
	found = {key: " ".join(str(reply.get(key) or "").split())[:140] for key in DETAILS}
	found["email"] = (validate_email_address(found["email"]) or "").strip() if found["email"] else ""
	return found


def capture(chat_id: str) -> dict:
	"""Read the customer's messages since the welcome and put what is new on the lead (a background
	job). Returns what was written."""
	row = welcome_row(chat_id)
	if not row or not _capturing(chat_id):
		return {}
	# All the customer wrote, before the welcome too: a name given in the first message counts.
	customer_text, messages = _customer_text(chat_id)
	if not messages:
		return {}
	# The customer answered the welcome: they get a lead, whether or not they gave any detail.
	record = crm_context.record_of(chat_id) or _new_lead(messages[-1].name)
	if not record:
		return {}  # the number may not get a lead (ignored, a known contact)
	got = _captured(row)
	found = details(row.reply_text or "", customer_text)
	new = {k: v for k, v in found.items() if v and not got.get(k)}
	if not new:
		return {}
	written = _fill(chat_id, record, messages[-1], new)
	if written:
		frappe.db.set_value(
			"Periskope Auto Reply",
			row.name,
			"captured",
			json.dumps({**got, **written}),
			update_modified=False,
		)
	return written


def details(welcome_text: str, customer_text: str) -> dict:
	"""What the customer gave: read by the AI, or by from_text when the AI cannot be asked or fails."""
	if customer_text and assistant.is_ready():
		try:
			return extract(welcome_text, customer_text)
		except Exception:
			frappe.log_error(title="Periskope: could not read the customer's details")
	return from_text(customer_text)


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NAME = re.compile(
	r"\b(?:my name is|this is|i am|i'm|myself)\s+(?!from\b)([a-z][a-z.' ]{0,40}?)(?=\s+(?:from|of|here)\b|[,.\n]|$)",
	re.I | re.M,
)
COMPANY = re.compile(
	r"(?:\b(?:my name is|this is|i am|i'm|myself)\s+[a-z.' ]{1,40}?\s+from|\bcompany(?: name)?(?: is)?\s*:?)\s+([a-z0-9&][\w&.'() -]{1,60}?)\s*(?:,\s*([^,\n]{2,60}))?\s*$",
	re.I | re.M,
)
CITY = re.compile(
	r"\b(?:city|address|located in|based in|i am from|i'm from)\s*(?:is|:)?\s+([^\n]{2,140})", re.I
)


def from_text(text: str) -> dict:
	"""The details a customer writes the usual ways ("This is Priya from Agam Technologies,
	Coimbatore", an email address), for when the AI cannot be asked. Misses more than the AI, never
	guesses."""
	found = dict.fromkeys(DETAILS, "")
	text = text or ""
	email = EMAIL.search(text)
	if email:
		found["email"] = (validate_email_address(email.group(0).rstrip(".")) or "").strip()
	name = NAME.search(text)
	if name:
		found["name"] = (
			name.group(1).strip(" .").title() if name.group(1).islower() else name.group(1).strip(" .")
		)
	company = COMPANY.search(text)
	if company:
		found["company"] = company.group(1).strip(" .")
		found["city_address"] = (company.group(2) or "").strip(" .")
	city = CITY.search(text)
	if city:
		found["city_address"] = city.group(1).strip(" .")
	return {key: " ".join(value.split())[:140] for key, value in found.items()}


def _new_lead(message: str) -> tuple[str, str] | None:
	lead = leads.create_lead_for_message(message)
	return ("CRM Lead", lead) if lead else None


def _fill(chat_id: str, record: tuple[str, str], message, new: dict) -> dict:
	"""Write what the record does not have yet; a value a person entered is never replaced."""
	doctype, name = record
	number = message.contact_number
	written = {}
	if doctype == "CRM Lead":
		lead = frappe.get_doc(doctype, name)
		values, keys = {}, []
		# The lead is made with the number, or a name the app read from their messages: both give way.
		placeholders = {"", number, " ".join(leads.split_name(leads.contact_name(message), number)).strip()}
		current = " ".join(filter(None, (lead.first_name, lead.last_name))).lstrip("+")
		if new.get("name") and current in placeholders:
			first, last = leads.split_name(new["name"], number)
			values.update(first_name=first, last_name=last)
			keys.append("name")
		if new.get("email") and not lead.email:
			values["email"] = new["email"]
			keys.append("email")

		def update():
			lead.update(values)
			# The app acts here, not a user: the job runs as Guest or Administrator.
			lead.save(ignore_permissions=True)
			return name

		if values and safety.crm_write(
			chat_id,
			"Update Lead",
			_("Filled {0} from the customer's WhatsApp reply").format(", ".join(keys)),
			update,
			target_doctype=doctype,
			target_name=name,
		):
			written.update({key: new[key] for key in keys})
	# Only what was written counts: in a dry run (test mode) the next message tries again.
	if new.get("company") and writeback.link_organization(chat_id, new["company"]):
		written["company"] = new["company"]
	if new.get("city_address") and writeback.upsert_note(chat_id, "address", new["city_address"]):
		written["city_address"] = new["city_address"]
	return written
