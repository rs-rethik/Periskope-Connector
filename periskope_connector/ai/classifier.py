"""Analyse Periskope Messages with Claude: a tag, severity, sentiment and a summary, and what to do next.

One answer per message also carries the AI's decisions (escalate, create a lead, show it on the
timeline, the tasks it hands out, what an attached image or document shows), and decisions.py carries
them out. No rule on a tag decides anything.
"""

import json
import re
import time

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from periskope_connector import attachments, decisions, tags
from periskope_connector.api.permission import require_admin
from periskope_connector.linking import link_message
from periskope_connector.setup_status import agent_on
from periskope_connector.system import insert_as_system, save_as_system
from periskope_connector.text_rules import analysis_skip_reason, readable_text, skip_fields

DEFAULT_MODEL = "claude-haiku-4-5"
ZAI_ANTHROPIC_URL = "https://api.z.ai/api/anthropic"
MAX_ATTEMPTS = 3
BATCH_SIZE = 20  # messages per run of the 5-minute catch-up job
QUEUED_GRACE_MINUTES = 3  # the catch-up leaves a message this long to its own job
GROUP_MIN_CONTEXT = 15
CONTEXT = 5  # earlier messages given with a 1-to-1 message
MIN_LENGTH = 2  # shorter texts are never sent to the AI
MAX_TOKENS = 800  # room for the JSON answer and its decisions
GROUP_MAX_TOKENS = 1200  # a group message can hand out several tasks
MAX_EXAMPLES = 30  # corrected messages fed back into the prompt
MAX_EXAMPLES_CHARS = 2400  # ... and their total length cap, so the prompt stays cacheable

PROVIDER_API = "Direct API"
PROVIDER_JARVIS = "Jarvis"

SENTIMENTS = ["Positive", "Neutral", "Negative"]
SEVERITIES = ["Low", "Medium", "High"]
ESCALATIONS = ["none", "managers", "md"]

INSTRUCTIONS = """You review WhatsApp messages between a company's sales reps and its customers, for a sales CRM. Messages may be in English, Tamil or Tanglish (Tamil written in English letters), and may come from 1-to-1 chats or from buyer groups. Classify the LAST message only, using the earlier messages as context. For each message you return structured data.

Rules:
- category is exactly one of the categories listed under Categories below. Pick the one whose description fits the message best.
- Messages labelled "Rep" are sent by our salesperson, and "Customer" messages come from the customer or prospect.
- severity is "High" for an angry customer, a rejection, or a risk of losing an order (also legal threats, fraud, abuse, or payments outside the company); "Medium" for a real problem that is not yet serious; otherwise "Low".
- sentiment is the customer's mood in this message (for a Rep message, the tone of the conversation).
- needs_reply is true only for a Customer message that expects an answer from the rep.
- follow_up_on is a local date and time (YYYY-MM-DD HH:MM) when the message sets a specific callback, meeting, or deadline, worked out from the current time you are given. Otherwise an empty string.
- summary is one line in English, at most 140 characters.
- mentioned_products, mentioned_amount, competitor_name: the exact values from the message, or empty strings.
- language is the English name of the language of the message."""


def get_settings():
	return frappe.get_single("Periskope Settings")


# --- Severity ------------------------------------------------------------------------------------


def _severity(value: str | None) -> str:
	value = (value or "").strip().capitalize()
	return value if value in SEVERITIES else "Low"


# --- Skipping ------------------------------------------------------------------------------------


TEXT_MESSAGE_TYPE = "text"


def can_read_attachment(doc, settings) -> bool:
	"""An image or PDF the configured AI can read itself (a Claude model on the Direct API)."""
	return (
		not uses_jarvis(settings)
		and is_claude(getattr(settings, "ai_model", None) or DEFAULT_MODEL)
		and attachments.readable(doc)
	)


def skip_reason(doc, settings) -> str | None:
	# Text chats, images and PDFs the AI can read, and attachments that carry words (a caption, or a
	# voice note's transcript).
	if doc.direction == "Outgoing":
		return "Sent by the rep"
	if can_read_attachment(doc, settings):
		return None
	if (doc.message_type or TEXT_MESSAGE_TYPE) != TEXT_MESSAGE_TYPE and not readable_text(doc):
		return "Not a text message"
	words = " ".join(
		p for p in ((doc.body or "").strip(), (getattr(doc, "transcript", "") or "").strip()) if p
	)
	if len(words) < MIN_LENGTH:
		return "Too short to tag"
	return analysis_skip_reason(words)


# --- Prompt and schema -------------------------------------------------------------------------


# Added to every prompt, so edited instructions still ask for it.
CUSTOMER_NAME_NOTE = (
	"customer_name is the name of the customer company or person the message is about, exactly as "
	"written in the message or the earlier messages (for example the name before an order number in a "
	"team group), or an empty string when none is named.\n"
	"contact_name is the personal name of the customer who wrote a Customer message, only when that "
	"person states it themselves in this or an earlier message (for example 'Hi, I am Ravi Kumar' or a "
	"sign-off). Never the rep's name, never a company, and an empty string when they have not said it."
)

# Added to every prompt too: what the agent does with the message is the AI's decision (decisions.py),
# not a rule on the tag.
DECISIONS_NOTE = (
	"You also decide what happens next; nothing is done by fixed rules.\n"
	"escalate is 'md' when the owner must hear of it now (a legal threat, abuse, a customer asked to pay "
	"into an account that is not the company's or saying they paid someone else, a big order about to "
	"be lost), 'managers' when a sales manager must step in (an angry customer, a complaint about "
	"quality or delivery, a rejection, a rep who promised and did not deliver, an urgent problem nobody "
	"is handling), else 'none'. Normal work is never escalated: the team sharing its own invoices, "
	"proforma invoices, quotations, bank details or reports, and colleagues handing out tasks. "
	"escalation_reason says in one English sentence what happened and what is at risk, or is empty.\n"
	"create_lead is true only for a Customer message in a 1-to-1 chat from someone who wants to buy, "
	"asks for prices, samples or a quotation, or places an order: a new business enquiry. False for "
	"colleagues, suppliers, job seekers, spam, greetings and wrong numbers.\n"
	"on_timeline is true when the message matters for the customer's lead or deal (a requirement, "
	"an order, a price, a date, a complaint, a decision, a document); false for greetings, thanks and "
	"chatter.\n"
	"attachment_summary: when an image or document is attached, one or two English sentences on what "
	"it is and its key facts (for an invoice, quotation or purchase order: the party, number, items, "
	"quantities, amounts and dates; for a screenshot: what it shows). Empty when nothing is attached.\n"
	"attachment_not_on_crm: when an image or document is attached and the CRM line under the message "
	"says the chat is not linked to a lead or deal, one English sentence, in your own words, on why "
	"this file is not on any lead or deal and what would put it there, judged from this message and "
	"the chat (who sent it, whether it is a customer's, a supplier's or the team's own document, "
	"whether a customer is named, whether it is a new enquiry). Empty when the chat is linked or "
	"nothing is attached."
)

# Added to every prompt too, so edited instructions still list the tasks (see chat_tasks.py).
TASKS_NOTE = (
	"tasks lists the work this message hands to people in a group chat, one item per job: a "
	"colleague telling others what to do (numbered points, @mentions, 'need to', 'follow up', "
	"'send'), or a customer in a customer group asking the team for something specific. An empty list "
	"for a 1-to-1 chat, for a message that only reports, asks a question or chats, and for work "
	"that is already done.\n"
	"For each item: task is a short instruction in English (at most 120 characters) with the "
	"quantities and items from the message; customer is the customer company the job is for, as "
	"written but without brand, product, place or person names ('Right Way - Tiger shade' gives "
	"'Right Way'), or an empty string; assignee_phone is the phone number @mentioned for that job "
	"(an @mention applies to every point after it until the next @mention), or an empty string when "
	"nobody is mentioned; due_date is YYYY-MM-DD when the message gives a day ('today', 'by "
	"Friday'), worked out from the current time, else an empty string."
)


def build_system_prompt(settings=None) -> str:
	prompt = f"{INSTRUCTIONS.strip()}\n\n{tags.prompt_block()}\n\n{CUSTOMER_NAME_NOTE}\n\n{DECISIONS_NOTE}\n\n{TASKS_NOTE}"

	business = (getattr(settings, "ai_business_context", None) or "").strip()
	if business:
		prompt = f"About the business you are working for:\n{business}\n\n{prompt}"

	examples = correction_examples()
	if examples:
		prompt = f"{prompt}\n\n{examples}"
	return prompt


def correction_examples() -> str:
	"""The sales team's latest category corrections, as few-shot examples. Empty when there are
	none. Bounded, so the system prompt stays small enough to cache."""
	rows = frappe.get_all(
		"Periskope Message",
		filters={"correct_category": ["is", "set"], "classification_status": "Done"},
		fields=["body", "correct_category"],
		order_by="modified desc",
		limit=MAX_EXAMPLES,
	)
	lines, total = [], 0
	for row in rows:
		text = " ".join((row.body or "").split())[:140]
		if not text:
			continue
		line = f"- {text} -> {row.correct_category}"
		if total + len(line) > MAX_EXAMPLES_CHARS:
			break
		lines.append(line)
		total += len(line)
	if not lines:
		return ""
	return "Earlier corrections by the sales team (classify messages like these the same way):\n" + "\n".join(
		lines
	)


def build_schema() -> dict:
	text = {"type": "string"}
	return {
		"type": "object",
		"properties": {
			"category": {"type": "string", "enum": tags.names()},
			"sentiment": {"type": "string", "enum": SENTIMENTS},
			"severity": {"type": "string", "enum": SEVERITIES},
			"summary": text,
			"confidence": {"type": "integer"},
			"needs_reply": {"type": "boolean"},
			"follow_up_on": text,
			"mentioned_products": text,
			"mentioned_amount": text,
			"competitor_name": text,
			"customer_name": text,
			"contact_name": text,
			"language": text,
			"escalate": {"type": "string", "enum": ESCALATIONS},
			"escalation_reason": text,
			"create_lead": {"type": "boolean"},
			"on_timeline": {"type": "boolean"},
			"attachment_summary": text,
			"attachment_not_on_crm": text,
			"tasks": {
				"type": "array",
				"items": {
					"type": "object",
					"properties": {
						"customer": text,
						"task": text,
						"assignee_phone": text,
						"due_date": text,
					},
					"required": ["customer", "task", "assignee_phone", "due_date"],
					"additionalProperties": False,
				},
			},
		},
		"required": [
			"category",
			"sentiment",
			"severity",
			"summary",
			"confidence",
			"needs_reply",
			"follow_up_on",
			"mentioned_products",
			"mentioned_amount",
			"competitor_name",
			"customer_name",
			"contact_name",
			"language",
			"escalate",
			"escalation_reason",
			"create_lead",
			"on_timeline",
			"attachment_summary",
			"attachment_not_on_crm",
			"tasks",
		],
		"additionalProperties": False,
	}


def _label(direction: str) -> str:
	return "Rep" if direction == "Outgoing" else "Customer"


GROUP_NOTE = (
	"This is a WhatsApp group chat{name}. Several people take part, and the group may be an internal "
	"team group where colleagues hand work to each other (for example asking someone to make a "
	"proforma invoice, PI, or a quotation) rather than a customer conversation. A message that "
	"@mentions a phone number is addressed to that person. Judge a short message by the earlier "
	"messages and by the message it replies to, and do not call it spam or a wrong number when the "
	"context explains it. Each line shows the sender's phone number."
)


def _speaker(m) -> str:
	"""'Rep' / 'Customer', plus the sender's number in a group so the AI can tell people apart."""
	label = _label(m.direction)
	if getattr(m, "is_group", 0) and getattr(m, "sender_phone", None):
		return f"{label} {m.sender_phone}"
	return label


def build_user_message(doc, history: list[dict], quoted: str | None = None) -> str:
	lines = ["Current time (site time): " + now_datetime().strftime("%Y-%m-%d %H:%M %A")]
	if doc.is_group:
		name = f' called "{doc.chat_name}"' if getattr(doc, "chat_name", None) else ""
		lines.append(GROUP_NOTE.format(name=name))
	lines.append("")
	if history:
		lines.append("Earlier messages in this chat (context only, oldest first):")
		for h in history:
			when = h.message_timestamp.strftime("%Y-%m-%d %H:%M") if h.message_timestamp else ""
			lines.append(f"[{_speaker(h)} {when}] {readable_text(h)[:500]}")
		lines.append("")
	when = doc.message_timestamp.strftime("%Y-%m-%d %H:%M") if doc.message_timestamp else ""
	if quoted:
		lines.append(f"The message below replies to this earlier message: {quoted.strip()[:500]}")
	lines.append("Message to classify:")
	lines.append(f"[{_speaker(doc)} {when}] {readable_text(doc)[:2000]}")
	if attachments.has_file(doc):
		name = getattr(doc, "media_filename", None) or ""
		lines.append(f"Attached to this message: {doc.message_type} {name}".strip())
		lines.append(crm_line(doc))
	return "\n".join(lines)


# Facts for the AI to explain a file that is not on a lead or deal in its own words.
UNLINKED = {
	"No Match": "no lead or deal in the CRM has this phone number",
	"Internal": "a team group where no customer could be identified from the group or the message",
}


def crm_line(doc) -> str:
	"""Where the attached file is saved in the CRM, or why the connector found no lead or deal."""
	record = attachments._record(doc)
	if record:
		return f"CRM: this chat is linked to {record[0]} {record[1]}, and the file is saved there."
	why = UNLINKED.get(
		getattr(doc, "link_status", None) or "", "it has not been matched to a lead or deal yet"
	)
	return f"CRM: this chat is not linked to any lead or deal ({why}), so the file is not saved on one."


def get_quoted_body(doc) -> str | None:
	"""Text of the message this one replies to. Periskope gives only the short WhatsApp id, which
	is the end of the stored message id."""
	if not doc.quoted_message_id or not doc.chat_id:
		return None
	return frappe.db.get_value(
		"Periskope Message",
		{"chat_id": doc.chat_id, "message_id": ["like", f"%{doc.quoted_message_id}"]},
		"body",
	)


def get_history(doc, limit: int) -> list[dict]:
	if not doc.chat_id or limit <= 0:
		return []
	filters = {"chat_id": doc.chat_id, "name": ["!=", doc.name], "is_deleted": 0}
	if doc.message_timestamp:
		filters["message_timestamp"] = ["<=", doc.message_timestamp]
	rows = frappe.get_all(
		"Periskope Message",
		filters=filters,
		fields=[
			"direction",
			"body",
			"message_type",
			"transcript",
			"message_timestamp",
			"sender_phone",
			"is_group",
		],
		order_by="message_timestamp desc",
		limit=limit,
	)
	return [r for r in reversed(rows) if readable_text(r)]


# --- Claude ------------------------------------------------------------------------------------


def _client(settings):
	import anthropic

	api_key = settings.get_password("anthropic_api_key", raise_exception=False)
	if not api_key:
		frappe.throw(_("AI API Key is not set in Periskope Settings"))
	kwargs = {"api_key": api_key, "max_retries": 2, "timeout": 60}
	base_url = (getattr(settings, "ai_base_url", None) or "").strip().rstrip("/")
	if not base_url and (getattr(settings, "ai_model", None) or "").startswith("glm"):
		# A GLM model can only be served by Z.ai; don't send its key to Anthropic by mistake.
		base_url = ZAI_ANTHROPIC_URL
	if base_url:
		# Other providers with an Anthropic-compatible address (e.g. Z.ai GLM) may expect the
		# key as a Bearer token; send it both ways.
		kwargs["base_url"] = base_url
		kwargs["default_headers"] = {"Authorization": f"Bearer {api_key}"}
	return anthropic.Anthropic(**kwargs)


def jarvis_available() -> bool:
	"""True when the Jarvis app is installed on this site."""
	return "jarvis" in frappe.get_installed_apps()


def available_providers() -> list[str]:
	return [PROVIDER_API, PROVIDER_JARVIS] if jarvis_available() else [PROVIDER_API]


def uses_jarvis(settings) -> bool:
	return (getattr(settings, "ai_provider", None) or PROVIDER_API) == PROVIDER_JARVIS


def _jarvis_llm_task():
	"""The Jarvis app's one-shot completion on its own configured model, or a clear error."""
	try:
		from jarvis.triggers.llm_task_client import LLMTaskError, llm_task_complete
	except ImportError:
		frappe.throw(_("The Jarvis app is not installed on this site. Install it or use the Direct API."))
	return llm_task_complete, LLMTaskError


JARVIS_REPLY_INSTRUCTIONS = """

Jarvis only accepts a JSON string as the reply. Reply with ONLY one JSON string literal whose text is the JSON object described below (no code fences, no commentary outside the quotes).
"""


def jarvis_complete(prompt: str, user: str, timeout: int = 60) -> str:
	"""Send one request to the model configured in Jarvis and return its reply text."""
	llm_task_complete, LLMTaskError = _jarvis_llm_task()
	try:
		return llm_task_complete(prompt, user, timeout=timeout)
	except LLMTaskError as e:
		frappe.throw(_("Jarvis could not complete the request: {0}").format(str(e)[:200]))


def is_claude(model: str) -> bool:
	return (model or "").startswith("claude")


JSON_REPLY_INSTRUCTIONS = """

Reply with ONLY one JSON object and nothing else (no explanation, no code fences), with exactly these keys:
category ({categories}), sentiment ("Positive", "Neutral" or "Negative"), severity ("Low", "Medium" or "High"), summary (string), confidence (integer 0 to 100), needs_reply (true or false), follow_up_on (string), mentioned_products (string), mentioned_amount (string), competitor_name (string), customer_name (string), contact_name (string), language (string), escalate ("none", "managers" or "md"), escalation_reason (string), create_lead (true or false), on_timeline (true or false), attachment_summary (string), attachment_not_on_crm (string), tasks (list of objects with customer, task, assignee_phone and due_date, all strings)."""


def json_reply_instructions() -> str:
	categories = ", ".join(f'"{name}"' for name in tags.names())
	return JSON_REPLY_INSTRUCTIONS.format(categories=categories)


def parse_json_reply(text: str) -> dict:
	"""Pull the JSON object out of a model reply that may add fences or extra words."""
	cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
	start, end = cleaned.find("{"), cleaned.rfind("}")
	if start == -1 or end <= start:
		raise ValueError("The AI did not return JSON")
	return json.loads(cleaned[start : end + 1])


def describe_error(e: Exception) -> str:
	import anthropic

	if isinstance(e, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
		return _("The AI provider rejected the API key")
	if isinstance(e, anthropic.NotFoundError):
		return _("The AI provider does not know this model name or address")
	if isinstance(e, anthropic.RateLimitError):
		return _("AI provider rate limit or balance reached, will retry")
	if isinstance(e, anthropic.APIConnectionError):
		return _("Could not reach the AI provider (check the AI API Address)")
	if isinstance(e, anthropic.APIStatusError):
		return _("The AI provider returned HTTP {0}: {1}").format(e.status_code, str(e.message)[:150])
	return str(e)[:300]


def ping(settings) -> str:
	"""Tiny request to prove the key and model work. Returns the model name."""
	if uses_jarvis(settings):
		jarvis_complete('Reply with ONLY the JSON string "OK".', "ping", timeout=30)
		return PROVIDER_JARVIS
	model = settings.ai_model or DEFAULT_MODEL
	try:
		_client(settings).messages.create(
			model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with OK."}]
		)
	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.throw(_("AI connection failed: {0}").format(describe_error(e)))
	return model


def _record_usage(message: str | None, settings, model: str, response, duration_ms: int):
	"""Save the token usage of one AI call for the spend report. Never raises."""
	try:
		usage = getattr(response, "usage", None) or type("U", (), {})()
		insert_as_system(
			{
				"doctype": "Periskope AI Usage",
				"message": message,
				"provider": PROVIDER_API,
				"model": model,
				"input_tokens": getattr(usage, "input_tokens", 0) or 0,
				"output_tokens": getattr(usage, "output_tokens", 0) or 0,
				"cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
				"cache_creation_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
				"duration_ms": duration_ms,
				"status": "OK",
			}
		)
	except Exception:
		frappe.log_error(title="Periskope: could not save the AI usage row")


def call_claude(settings, doc) -> dict:
	# A short group message means little without what was said before it.
	history = get_history(doc, GROUP_MIN_CONTEXT if doc.is_group else CONTEXT)
	model = settings.ai_model or DEFAULT_MODEL
	prompt = build_system_prompt(settings)

	if uses_jarvis(settings):
		reply = jarvis_complete(
			prompt + json_reply_instructions() + JARVIS_REPLY_INSTRUCTIONS,
			build_user_message(doc, history, get_quoted_body(doc)),
		)
		return parse_json_reply(reply)

	request = {
		"model": model,
		# The reply budget. JSON answers fit well below this; reasoning models (Sonnet/Opus 5.5)
		# need more room and get it by raising the setting.
		"max_tokens": GROUP_MAX_TOKENS if doc.is_group else MAX_TOKENS,
		"messages": [{"role": "user", "content": build_user_message(doc, history, get_quoted_body(doc))}],
	}
	attachment = attachments.content_block(doc) if is_claude(model) else None
	if attachment:
		# The image or PDF itself, so the AI reads what it shows.
		request["messages"][0]["content"] = [
			attachment,
			{"type": "text", "text": request["messages"][0]["content"]},
		]
	if is_claude(model):
		# The instructions rarely change, so let Anthropic cache them, and have the API
		# enforce the answer format.
		request["system"] = [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]
		request["output_config"] = {"format": {"type": "json_schema", "schema": build_schema()}}
	else:
		# Other providers: no Claude-only options, so ask for JSON in the prompt and parse it.
		request["system"] = prompt + json_reply_instructions()

	started = time.monotonic()
	response = _client(settings).messages.create(**request)
	_record_usage(doc.name, settings, model, response, int((time.monotonic() - started) * 1000))
	if response.stop_reason in ("refusal", "max_tokens"):
		raise ValueError(f"The AI stopped early: {response.stop_reason}")
	text = next((b.text for b in response.content if b.type == "text"), None)
	if not text:
		raise ValueError("The AI returned no text")
	return json.loads(text) if is_claude(model) else parse_json_reply(text)


# --- Saving the result -------------------------------------------------------------------------


def apply_result(doc, result: dict):
	category = result.get("category")
	if category not in tags.names():
		category = tags.default_tag()

	follow_up = None
	if result.get("follow_up_on"):
		try:
			follow_up = get_datetime(result["follow_up_on"])
		except (ValueError, TypeError):
			follow_up = None

	doc.ai_category = category
	doc.sentiment = result.get("sentiment") if result.get("sentiment") in SENTIMENTS else "Neutral"
	doc.severity = _severity(result.get("severity"))
	doc.ai_summary = (result.get("summary") or "")[:140]
	try:
		confidence = int(float(result.get("confidence") or 0))
	except (TypeError, ValueError):
		confidence = 0
	doc.ai_confidence = max(0, min(100, confidence))
	doc.needs_reply = 1 if result.get("needs_reply") else 0
	doc.follow_up_on = follow_up
	doc.mentioned_products = result.get("mentioned_products") or ""
	doc.mentioned_amount = (result.get("mentioned_amount") or "")[:140]
	doc.competitor_name = (result.get("competitor_name") or "")[:140]
	doc.mentioned_customer = (result.get("customer_name") or "")[:140]
	doc.contact_name = (result.get("contact_name") or "")[:140]
	doc.language = (result.get("language") or "")[:140]
	escalation = {"managers": "Managers", "md": "MD"}.get(result.get("escalate") or "", "")
	doc.escalation = escalation
	doc.escalation_reason = (result.get("escalation_reason") or "")[:500] if escalation else ""
	doc.on_timeline = 1 if result.get("on_timeline") else 0
	if result.get("attachment_summary"):
		doc.attachment_summary = result["attachment_summary"][:1000]
	doc.classification_status = "Done"
	doc.classified_at = now_datetime()
	doc.classification_error = ""
	save_as_system(doc)
	link_message(doc.name)
	decisions.act(doc.name, result)
	if attachments.has_file(doc):
		_note_unsaved_attachment(doc.name, result)
	# Keep the CRM record's summary, notes and follow-ups current (throttled per chat).
	from periskope_connector.ai import analyse

	analyse.queue(doc.chat_id)


def _note_unsaved_attachment(message: str, result: dict):
	"""The AI's reason the file is not on a lead or deal, kept only while that is still so (a lead
	created for the message, or a link found after the analysis, puts the file there)."""
	linked = frappe.db.get_value("Periskope Message", message, ["crm_lead", "crm_deal"], as_dict=True)
	reason = "" if (linked.crm_lead or linked.crm_deal) else (result.get("attachment_not_on_crm") or "")
	_mark(message, attachment_not_on_crm=reason[:1000])


def _mark(name: str, **values):
	frappe.db.set_value("Periskope Message", name, values, update_modified=False)


def queue(message: str):
	"""Analyse a message as soon as it is saved (run_batch catches up on any job that never ran)."""
	frappe.enqueue(
		"periskope_connector.ai.classifier.classify_now",
		message=message,
		queue="short",
		job_id=f"periskope_classify::{message}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def classify_now(message: str):
	"""Background job: analyse one message, unless the AI is not ready (the batch retries later)."""
	from periskope_connector.health import budget_exceeded

	if not _ready_to_run() or budget_exceeded():
		return
	_classify_and_commit(message)


def classify(message: str):
	"""Classify one Periskope Message."""
	settings = get_settings()
	doc = frappe.get_doc("Periskope Message", message)
	if doc.classification_status in ("Done", "Skipped"):
		return

	reason = skip_reason(doc, settings)
	if reason:
		_mark(message, **skip_fields(reason))
		return

	attempts = (doc.classification_attempts or 0) + 1
	frappe.db.savepoint("periskope_classify")
	try:
		result = call_claude(settings, doc)
		doc.classification_attempts = attempts
		apply_result(doc, result)
	except Exception as e:
		frappe.db.rollback(save_point="periskope_classify")
		_mark(
			message,
			classification_status="Failed",
			classification_attempts=attempts,
			classification_error=describe_error(e),
		)
		if not _is_expected_error(e):
			frappe.log_error(title=f"Periskope: could not classify message {message}")


def _is_expected_error(e: Exception) -> bool:
	import anthropic

	return isinstance(e, anthropic.APIError | frappe.ValidationError)


# --- Retry, backfill, reclassify -----------------------------------------------------------------


def _ready_to_run() -> bool:
	settings = get_settings()
	if not agent_on(settings):
		return False
	if uses_jarvis(settings):
		return True
	return bool(settings.get_password("anthropic_api_key", raise_exception=False))


def _pending(limit: int) -> list[str]:
	return frappe.get_all(
		"Periskope Message",
		filters={
			"classification_attempts": ["<", MAX_ATTEMPTS],
			"direction": "Incoming",
			# a newer one is still with its own job (queue)
			"modified": ["<", add_to_date(now_datetime(), minutes=-QUEUED_GRACE_MINUTES)],
		},
		or_filters=[
			["classification_status", "is", "not set"],
			["classification_status", "in", ["Pending", "Failed"]],
		],
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)


def _record_failure(name: str, error: Exception):
	"""Count the attempt, so a message that keeps failing stops being retried after MAX_ATTEMPTS."""
	try:
		attempts = (frappe.db.get_value("Periskope Message", name, "classification_attempts") or 0) + 1
		_mark(
			name,
			classification_status="Failed",
			classification_attempts=attempts,
			classification_error=describe_error(error),
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Periskope: could not record the failure of message {name}")


def _classify_and_commit(name: str):
	"""Classify one message and commit its result on its own.

	A failure (or a failed commit) is rolled back and logged, and the message is marked Failed,
	so one bad message can neither stop the rest of the batch nor undo the ones already saved.
	"""
	try:
		classify(name)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Periskope: could not save the classification of message {name}")
		_record_failure(name, e)


def run_batch():
	"""Scheduler, every 5 minutes: classify up to BATCH_SIZE waiting customer messages, oldest first.
	Failed ones are tried again on the next run, up to MAX_ATTEMPTS times. Paused when the
	monthly token budget is used up (health.spend_check tells the managers).

	A single failing message is handled by _classify_and_commit(). Anything else (for example
	the database being unreachable) is written to the Error Log and raised again, so it also
	shows as failed in the Scheduled Job Log.
	"""
	try:
		if not _ready_to_run():
			return
		from periskope_connector.health import budget_exceeded

		if budget_exceeded():
			frappe.logger("periskope_connector").info("AI classification paused: monthly budget reached")
			return
		for name in _pending(BATCH_SIZE):
			_classify_and_commit(name)
	except Exception:
		frappe.log_error(title="Periskope: AI classification run failed")
		raise


def classify_pending(limit: int = 500):
	"""Backfill. Run: bench --site <site> execute periskope_connector.ai.classifier.classify_pending"""
	if not _ready_to_run():
		return "Classification is not enabled or the AI API key is missing"
	names = _pending(limit)
	for name in names:
		_classify_and_commit(name)
	return len(names)


@frappe.whitelist()
def reclassify(message: str) -> None:
	require_admin()
	_mark(message, classification_status="Pending", classification_attempts=0, classification_error="")
	queue(message)
