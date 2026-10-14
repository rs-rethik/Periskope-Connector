"""Classify Periskope Messages with Claude: one of six categories, plus severity, sentiment and a summary.

The category then drives what the app does: a new enquiry from an unknown number creates a CRM Lead, and
linked messages are shown in the CRM timeline. Nothing else in the CRM is changed here.
"""

import json
import re
import time

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now_datetime

from periskope_connector import actions, tags
from periskope_connector.linking import link_message
from periskope_connector.setup_status import feature_on
from periskope_connector.system import insert_as_system, save_as_system
from periskope_connector.text_rules import analysis_skip_reason, skip_fields

DEFAULT_MODEL = "claude-haiku-4-5"
ZAI_ANTHROPIC_URL = "https://api.z.ai/api/anthropic"
MAX_ATTEMPTS = 3
BATCH_SIZE = 20  # messages per run of the 5-minute job
GROUP_MIN_CONTEXT = 15
DEFAULT_CONTEXT = 5
DEFAULT_MAX_TOKENS = 400  # room for the JSON answer; raise for reasoning models
MAX_EXAMPLES = 30  # corrected messages fed back into the prompt
MAX_EXAMPLES_CHARS = 2400  # ... and their total length cap, so the prompt stays cacheable

PROVIDER_API = "Direct API"
PROVIDER_JARVIS = "Jarvis"

SENTIMENTS = ["Positive", "Neutral", "Negative"]
SEVERITIES = ["Low", "Medium", "High"]

# The default instructions. Management can edit the live copy in Periskope Settings; this text
# is used when that field is empty and by "Reset to default".
DEFAULT_INSTRUCTIONS = """You review WhatsApp messages between a company's sales reps and its customers, for a sales CRM. Messages may be in English, Tamil or Tanglish (Tamil written in English letters), and may come from 1-to-1 chats or from buyer groups. Classify the LAST message only, using the earlier messages as context. For each message you return structured data.

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


def skip_reason(doc, settings) -> str | None:
	# For now only text chats are analysed: no images, documents, voice notes, contacts, etc.
	if doc.direction == "Outgoing":
		return "Sent by the rep"
	if (doc.message_type or TEXT_MESSAGE_TYPE) != TEXT_MESSAGE_TYPE:
		return "Not a text message"
	if doc.is_group and not settings.classify_group_chats:
		return "Group chat"
	min_length = flt(getattr(settings, "min_length", None)) or 2
	if len((doc.body or "").strip()) < min_length:
		return "Too short to tag"
	return analysis_skip_reason(doc.body)


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


def build_system_prompt(settings=None) -> str:
	instructions = (getattr(settings, "ai_instructions", None) or "").strip()
	if not instructions:
		instructions = DEFAULT_INSTRUCTIONS.strip()
	prompt = f"{instructions}\n\n{tags.prompt_block()}\n\n{CUSTOMER_NAME_NOTE}"

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
			lines.append(f"[{_speaker(h)} {when}] {(h.body or '').strip()[:500]}")
		lines.append("")
	when = doc.message_timestamp.strftime("%Y-%m-%d %H:%M") if doc.message_timestamp else ""
	if quoted:
		lines.append(f"The message below replies to this earlier message: {quoted.strip()[:500]}")
	lines.append("Message to classify:")
	lines.append(f"[{_speaker(doc)} {when}] {(doc.body or '').strip()[:2000]}")
	return "\n".join(lines)


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
	filters = {"chat_id": doc.chat_id, "name": ["!=", doc.name], "message_type": TEXT_MESSAGE_TYPE}
	if doc.message_timestamp:
		filters["message_timestamp"] = ["<=", doc.message_timestamp]
	rows = frappe.get_all(
		"Periskope Message",
		filters=filters,
		fields=["direction", "body", "message_timestamp", "sender_phone", "is_group"],
		order_by="message_timestamp desc",
		limit=limit,
	)
	return list(reversed(rows))


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
category ({categories}), sentiment ("Positive", "Neutral" or "Negative"), severity ("Low", "Medium" or "High"), summary (string), confidence (integer 0 to 100), needs_reply (true or false), follow_up_on (string), mentioned_products (string), mentioned_amount (string), competitor_name (string), customer_name (string), contact_name (string), language (string)."""


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


OPTIMIZER_SYSTEM_PROMPT = """You are an expert prompt engineer. You improve the instructions given to an AI that classifies WhatsApp messages between a company's sales reps and customers, for a sales CRM.

Rewrite the instructions you are given so the AI follows them more reliably. Rules:
- Keep the same purpose, and keep every field the AI must fill in: category, sentiment, severity, summary, confidence, needs_reply, follow_up_on, mentioned_products, mentioned_amount, competitor_name, customer_name, contact_name, language.
- Keep the category names exactly as they are written under Categories, and their meanings. Never invent other categories. If the instructions do not list the categories, leave that out: the list is added separately.
- Be specific and unambiguous. Remove repetition and contradictions. Add short rules for edge cases that are missing, such as mixed-language messages, group chats, messages from the rep, and very short messages.
- Do not make it much longer than it needs to be.
- Reply with ONLY the improved instructions, as plain text. No explanation, no heading, no code fences."""


def optimize_instructions(settings, instructions: str, goal: str = "") -> str:
	"""Ask the configured AI to improve the tagging instructions. Returns the new text; nothing is saved."""
	if uses_jarvis(settings):
		# Jarvis caps replies at about 1000 tokens, too small for a full rewrite of the instructions.
		frappe.throw(
			_("Improve Instructions with AI needs the Direct API. Switch the AI Provider to use it.")
		)
	instructions = (instructions or "").strip() or DEFAULT_INSTRUCTIONS.strip()
	user = f"Current instructions:\n<instructions>\n{instructions}\n</instructions>"
	if (goal or "").strip():
		user += f"\n\nWhat the user wants improved:\n{goal.strip()[:1000]}"

	try:
		response = _client(settings).messages.create(
			model=settings.ai_model or DEFAULT_MODEL,
			max_tokens=4096,
			system=OPTIMIZER_SYSTEM_PROMPT,
			messages=[{"role": "user", "content": user}],
		)
	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.throw(_("Could not improve the instructions: {0}").format(describe_error(e)))

	text = next((b.text for b in response.content if b.type == "text"), "") or ""
	text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text.strip()).strip()
	if response.stop_reason == "max_tokens" or len(text) < 100:
		frappe.throw(_("The AI did not return usable instructions. Please try again."))
	return text


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


def max_tokens(settings) -> int:
	return int(flt(getattr(settings, "ai_max_tokens", None)) or DEFAULT_MAX_TOKENS)


def call_claude(settings, doc) -> dict:
	limit = settings.context_messages or DEFAULT_CONTEXT
	if doc.is_group:
		# A short group message means little without what was said before it.
		limit = max(limit, GROUP_MIN_CONTEXT)
	history = get_history(doc, limit)
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
		"max_tokens": max_tokens(settings),
		"messages": [{"role": "user", "content": build_user_message(doc, history, get_quoted_body(doc))}],
	}
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
	doc.classification_status = "Done"
	doc.classified_at = now_datetime()
	doc.classification_error = ""
	save_as_system(doc)
	link_message(doc.name)
	actions.run(doc.name)


def _mark(name: str, **values):
	frappe.db.set_value("Periskope Message", name, values, update_modified=False)


def classify(message: str):
	"""Background job: classify one Periskope Message."""
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
	if not feature_on("enable_classification", settings):
		return False
	if uses_jarvis(settings):
		return True
	return bool(settings.get_password("anthropic_api_key", raise_exception=False))


def _pending(limit: int) -> list[str]:
	return frappe.get_all(
		"Periskope Message",
		filters={"classification_attempts": ["<", MAX_ATTEMPTS], "direction": "Incoming"},
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
	frappe.only_for("System Manager")
	_mark(message, classification_status="Pending", classification_attempts=0, classification_error="")
	frappe.enqueue(
		"periskope_connector.ai.classifier.classify",
		message=message,
		queue="short",
		enqueue_after_commit=True,
	)
