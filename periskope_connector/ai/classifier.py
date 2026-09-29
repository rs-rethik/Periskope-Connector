"""Tag Periskope Messages with Claude and turn the tags into suggested FCRM actions.

Nothing here changes CRM records. It only records what the tags suggest; later phases act on it.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from periskope_connector.text_rules import analysis_skip_reason

DEFAULT_MODEL = "claude-haiku-4-5"
ZAI_ANTHROPIC_URL = "https://api.z.ai/api/anthropic"
MAX_ATTEMPTS = 3
MAX_TAGS = 5
RETRY_BATCH = 50

SENTIMENTS = ["Positive", "Neutral", "Negative"]
SEVERITIES = ["None", "Low", "Medium", "High", "Critical"]

# Strongest first: decides the headline `suggested_action` on a message.
ACTION_PRIORITY = [
	"Escalate",
	"Mark Lost",
	"Create Lead",
	"Update Deal Status",
	"Update Lead Status",
	"Create Task",
]

# The default instructions. Management can edit the live copy in Periskope Settings; this text
# is used when that field is empty and by "Reset to default". {tag_list} is replaced by the
# enabled tags (and appended automatically if an edited copy leaves it out).
DEFAULT_INSTRUCTIONS = """You classify WhatsApp messages between a company's sales reps and their customers or prospects, for a sales CRM. For each message you return structured data.

Rules:
- Choose 1 to 5 tags, only from the tag list below, and set primary_tag to the single best one. Use a tag only when the message itself supports it. The earlier messages are context only: do not tag the earlier messages again.
- Messages labelled "Rep" are sent by our salesperson, and "Customer" messages come from the customer or prospect. Rep messages can carry tags too, for example Quotation Shared, Follow-up Promised by Rep, or Unauthorised Discount / Commitment by Rep.
- sentiment is the customer's mood in this message (for a Rep message, the tone of the conversation).
- severity is "None" unless there is a problem management should know about: Low, Medium, High, or Critical (legal threats, fraud, payments outside the company, abuse).
- needs_reply is true only for a Customer message that expects an answer from the rep.
- follow_up_on is a local date and time (YYYY-MM-DD HH:MM) when the message sets a specific callback, meeting, or deadline, worked out from the current time you are given. Otherwise an empty string.
- summary is one short sentence in English.
- mentioned_products, mentioned_amount, competitor_name: the exact values from the message, or empty strings.
- language is the English name of the language of the message. Messages may be in any language, or mixed (for example Hindi or Tamil written in English letters).

Available tags, by category:
{tag_list}"""


def get_settings():
	return frappe.get_single("Periskope Settings")


# --- Tags -> suggested CRM actions (no AI involved) -------------------------------------------


def enabled_tags() -> list[dict]:
	return frappe.get_all(
		"Periskope Message Tag",
		filters={"enabled": 1},
		fields=[
			"name",
			"tag_name",
			"category",
			"description",
			"fcrm_action",
			"lead_source",
			"target_lead_status",
			"target_deal_status",
			"lost_reason",
			"task_title",
			"task_priority",
			"escalation_severity",
		],
		order_by="category asc, tag_name asc",
	)


def resolve_actions(tags: list[dict], follow_up_on=None) -> tuple[list[dict], str]:
	"""Return (all suggested actions, headline action) for a list of tag dicts."""
	actions = []
	for t in tags:
		main = t.get("fcrm_action") or "None"
		tag = t.get("tag_name") or t.get("name")

		if main == "Create Lead":
			actions.append({"action": "Create Lead", "tag": tag, "source": t.get("lead_source")})
		if t.get("target_deal_status"):
			actions.append(
				{"action": "Update Deal Status", "tag": tag, "deal_status": t["target_deal_status"]}
			)
		if t.get("target_lead_status"):
			actions.append(
				{"action": "Update Lead Status", "tag": tag, "lead_status": t["target_lead_status"]}
			)
		if main == "Mark Lost" or t.get("lost_reason"):
			actions.append({"action": "Mark Lost", "tag": tag, "lost_reason": t.get("lost_reason")})
		if main == "Create Task" or t.get("task_title"):
			actions.append(
				{
					"action": "Create Task",
					"tag": tag,
					"title": t.get("task_title") or tag,
					"priority": t.get("task_priority") or "Medium",
					"due": str(follow_up_on) if follow_up_on else None,
				}
			)
		if main == "Escalate" or t.get("escalation_severity"):
			actions.append(
				{
					"action": "Escalate",
					"tag": tag,
					"severity": t.get("escalation_severity") or "Medium",
				}
			)

	present = {a["action"] for a in actions}
	headline = next((a for a in ACTION_PRIORITY if a in present), "None")
	return actions, headline


def _max_severity(*values: str | None) -> str:
	ranked = [v for v in values if v in SEVERITIES]
	return max(ranked, key=SEVERITIES.index) if ranked else "None"


# --- Skipping ------------------------------------------------------------------------------------


TEXT_MESSAGE_TYPE = "chat"  # Periskope's type for a plain text message


def skip_reason(doc, settings) -> str | None:
	# For now only text chats are analysed: no images, documents, voice notes, contacts, etc.
	if (doc.message_type or TEXT_MESSAGE_TYPE) != TEXT_MESSAGE_TYPE:
		return "Not a text message"
	if doc.is_group and not settings.classify_group_chats:
		return "Group chat"
	return analysis_skip_reason(doc.body)


# --- Prompt and schema -------------------------------------------------------------------------


TAG_LIST_MARKER = "{tag_list}"


def build_system_prompt(tags: list[dict], settings=None) -> str:
	by_category: dict[str, list[str]] = {}
	for t in tags:
		by_category.setdefault(t["category"], []).append(f"- {t['tag_name']}: {t['description']}")
	tag_list = "\n\n".join(f"{cat}\n" + "\n".join(lines) for cat, lines in by_category.items())

	instructions = (getattr(settings, "ai_instructions", None) or "").strip() or DEFAULT_INSTRUCTIONS.strip()
	if TAG_LIST_MARKER in instructions:
		# replace(), not format(): the admin's text may contain other curly braces
		prompt = instructions.replace(TAG_LIST_MARKER, tag_list)
	else:
		prompt = f"{instructions}\n\nAvailable tags, by category:\n{tag_list}"

	business = (getattr(settings, "ai_business_context", None) or "").strip()
	if business:
		prompt = f"About the business you are working for:\n{business}\n\n{prompt}"
	return prompt


def build_schema(tag_names: list[str]) -> dict:
	text = {"type": "string"}
	return {
		"type": "object",
		"properties": {
			"tags": {"type": "array", "items": {"type": "string", "enum": tag_names}},
			"primary_tag": {"type": "string", "enum": tag_names},
			"sentiment": {"type": "string", "enum": SENTIMENTS},
			"severity": {"type": "string", "enum": SEVERITIES},
			"summary": text,
			"confidence": {"type": "integer"},
			"needs_reply": {"type": "boolean"},
			"follow_up_on": text,
			"mentioned_products": text,
			"mentioned_amount": text,
			"competitor_name": text,
			"language": text,
		},
		"required": [
			"tags",
			"primary_tag",
			"sentiment",
			"severity",
			"summary",
			"confidence",
			"needs_reply",
			"follow_up_on",
			"mentioned_products",
			"mentioned_amount",
			"competitor_name",
			"language",
		],
		"additionalProperties": False,
	}


def _label(direction: str) -> str:
	return "Rep" if direction == "Outgoing" else "Customer"


def build_user_message(doc, history: list[dict]) -> str:
	lines = ["Current time (site time): " + now_datetime().strftime("%Y-%m-%d %H:%M %A")]
	if doc.is_group:
		lines.append(
			"This is a WhatsApp group chat. Several people take part: a Customer message is from "
			"any participant other than our rep."
		)
	lines.append("")
	if history:
		lines.append("Earlier messages in this chat (context only, oldest first):")
		for h in history:
			when = h.message_timestamp.strftime("%Y-%m-%d %H:%M") if h.message_timestamp else ""
			lines.append(f"[{_label(h.direction)} {when}] {(h.body or '').strip()[:500]}")
		lines.append("")
	when = doc.message_timestamp.strftime("%Y-%m-%d %H:%M") if doc.message_timestamp else ""
	lines.append("Message to classify:")
	lines.append(f"[{_label(doc.direction)} {when}] {(doc.body or '').strip()[:2000]}")
	return "\n".join(lines)


def get_history(doc, limit: int) -> list[dict]:
	if not doc.chat_id or limit <= 0:
		return []
	filters = {"chat_id": doc.chat_id, "name": ["!=", doc.name], "message_type": TEXT_MESSAGE_TYPE}
	if doc.message_timestamp:
		filters["message_timestamp"] = ["<=", doc.message_timestamp]
	rows = frappe.get_all(
		"Periskope Message",
		filters=filters,
		fields=["direction", "body", "message_timestamp"],
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


def is_claude(model: str) -> bool:
	return (model or "").startswith("claude")


JSON_REPLY_INSTRUCTIONS = """

Reply with ONLY one JSON object and nothing else (no explanation, no code fences), with exactly these keys:
tags (array of 1 to 5 tag names from the list above), primary_tag (one tag name from the list), sentiment ("Positive", "Neutral" or "Negative"), severity ("None", "Low", "Medium", "High" or "Critical"), summary (string), confidence (integer 0 to 100), needs_reply (true or false), follow_up_on (string), mentioned_products (string), mentioned_amount (string), competitor_name (string), language (string)."""


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
		return "The AI provider rejected the API key"
	if isinstance(e, anthropic.NotFoundError):
		return "The AI provider does not know this model name or address"
	if isinstance(e, anthropic.RateLimitError):
		return "AI provider rate limit or balance reached, will retry"
	if isinstance(e, anthropic.APIConnectionError):
		return "Could not reach the AI provider (check the AI API Address)"
	if isinstance(e, anthropic.APIStatusError):
		return f"The AI provider returned HTTP {e.status_code}: {str(e.message)[:150]}"
	return str(e)[:300]


def ping(settings) -> str:
	"""Tiny request to prove the key and model work. Returns the model name."""
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


OPTIMIZER_SYSTEM_PROMPT = """You are an expert prompt engineer. You improve the instructions given to an AI that tags WhatsApp messages between a company's sales reps and customers, for a sales CRM.

Rewrite the instructions you are given so the AI follows them more reliably. Rules:
- Keep the same purpose, and keep every field the AI must fill in: tags, primary_tag, sentiment, severity, summary, confidence, needs_reply, follow_up_on, mentioned_products, mentioned_amount, competitor_name, language.
- Keep the text {tag_list} exactly once, on its own line, where the list of tags belongs. Never write out or invent tags yourself.
- Be specific and unambiguous. Remove repetition and contradictions. Add short rules for edge cases that are missing, such as mixed-language messages, group chats, messages from the rep, and very short messages.
- Do not make it much longer than it needs to be.
- Reply with ONLY the improved instructions, as plain text. No explanation, no heading, no code fences."""


def optimize_instructions(settings, instructions: str, goal: str = "") -> str:
	"""Ask the configured AI to improve the tagging instructions. Returns the new text; nothing is saved."""
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
	if TAG_LIST_MARKER not in text:
		text += f"\n\nAvailable tags, by category:\n{TAG_LIST_MARKER}"
	return text


def call_claude(settings, doc, tags: list[dict]) -> dict:
	tag_names = [t["tag_name"] for t in tags]
	history = get_history(doc, settings.context_messages or 0)
	model = settings.ai_model or DEFAULT_MODEL
	prompt = build_system_prompt(tags, settings)

	request = {
		"model": model,
		# Room for the reasoning that Sonnet 5.5 / Opus 5.5 always do before the JSON answer.
		"max_tokens": 4096,
		"messages": [{"role": "user", "content": build_user_message(doc, history)}],
	}
	if is_claude(model):
		# The tag list is long and rarely changes, so let Anthropic cache it, and have the API
		# enforce the answer format.
		request["system"] = [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]
		request["output_config"] = {"format": {"type": "json_schema", "schema": build_schema(tag_names)}}
	else:
		# Other providers: no Claude-only options, so ask for JSON in the prompt and parse it.
		request["system"] = prompt + JSON_REPLY_INSTRUCTIONS

	response = _client(settings).messages.create(**request)
	if response.stop_reason in ("refusal", "max_tokens"):
		raise ValueError(f"The AI stopped early: {response.stop_reason}")
	text = next((b.text for b in response.content if b.type == "text"), None)
	if not text:
		raise ValueError("The AI returned no text")
	return json.loads(text) if is_claude(model) else parse_json_reply(text)


# --- Saving the result -------------------------------------------------------------------------


def apply_result(doc, result: dict, tags: list[dict]):
	by_name = {t["tag_name"]: t for t in tags}
	chosen = []
	for name in result.get("tags") or []:
		if name in by_name and name not in chosen:
			chosen.append(name)
	chosen = chosen[:MAX_TAGS]
	primary = result.get("primary_tag")
	if primary not in chosen:
		primary = chosen[0] if chosen else None

	follow_up = None
	if result.get("follow_up_on"):
		try:
			follow_up = get_datetime(result["follow_up_on"])
		except (ValueError, TypeError):
			follow_up = None

	chosen_tags = [by_name[n] for n in chosen]
	actions, headline = resolve_actions(chosen_tags, follow_up)
	severity = _max_severity(result.get("severity"), *[t.get("escalation_severity") for t in chosen_tags])

	doc.primary_tag = primary
	doc.set("tags", [{"tag": n} for n in chosen])
	doc.sentiment = result.get("sentiment") if result.get("sentiment") in SENTIMENTS else "Neutral"
	doc.severity = severity
	doc.ai_summary = (result.get("summary") or "")[:500]
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
	doc.language = (result.get("language") or "")[:140]
	doc.suggested_actions = json.dumps(actions, indent=1, default=str)
	doc.suggested_action = headline
	doc.classification_status = "Done"
	doc.classified_at = now_datetime()
	doc.classification_error = ""
	doc.save(ignore_permissions=True)


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
		_mark(message, classification_status="Skipped", classification_error=reason)
		return

	tags = enabled_tags()
	if not tags:
		_mark(message, classification_status="Skipped", classification_error="No enabled tags")
		return

	attempts = (doc.classification_attempts or 0) + 1
	frappe.db.savepoint("periskope_classify")
	try:
		result = call_claude(settings, doc, tags)
		doc.classification_attempts = attempts
		apply_result(doc, result, tags)
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
	return bool(
		settings.enable_classification and settings.get_password("anthropic_api_key", raise_exception=False)
	)


def _pending(limit: int) -> list[str]:
	return frappe.get_all(
		"Periskope Message",
		filters={"classification_attempts": ["<", MAX_ATTEMPTS]},
		or_filters=[
			["classification_status", "is", "not set"],
			["classification_status", "in", ["Pending", "Failed"]],
		],
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)


def retry_failed_classifications():
	"""Hourly scheduler: pick up Pending/Failed messages, a limited batch per run."""
	if not _ready_to_run():
		return
	for name in _pending(RETRY_BATCH):
		classify(name)
		frappe.db.commit()  # keep each result even if a later one crashes


def classify_pending(limit: int = 500):
	"""Backfill. Run: bench --site <site> execute periskope_connector.ai.classifier.classify_pending"""
	if not _ready_to_run():
		return "Classification is not enabled or the Anthropic API key is missing"
	names = _pending(limit)
	for name in names:
		classify(name)
		frappe.db.commit()
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
