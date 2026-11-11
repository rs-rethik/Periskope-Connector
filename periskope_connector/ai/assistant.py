"""One structured AI call for the assistant features (knowledge, learning, auto reply).

It reuses the classifier's client, provider choice and usage log, so these calls count in the same
token budget as the classification. The caller gives a system prompt, the data and a JSON schema.
"""

import json
import time

import frappe
from frappe import _

from periskope_connector.ai import classifier

DEFAULT_MAX_TOKENS = 2000


def readiness(settings=None) -> tuple[bool, str]:
	"""(True, "") when an AI call may be made, else (False, why): no AI key, or the monthly token
	budget is used up. Checked before every call the agent makes."""
	from periskope_connector.health import budget_exceeded

	settings = settings or classifier.get_settings()
	if budget_exceeded():
		return False, "budget"
	if classifier.uses_jarvis(settings):
		return True, ""
	if not settings.get_password("anthropic_api_key", raise_exception=False):
		return False, "no_key"
	return True, ""


def is_ready(settings=None) -> bool:
	"""True when an AI key (or Jarvis) is set and the monthly token budget is not used up."""
	return readiness(settings)[0]


def complete_json(system: str, user: str, schema: dict, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
	"""Ask the model for one JSON object that follows `schema`. Raises on any failure."""
	from periskope_connector.text_rules import UNTRUSTED_TEXT_RULE

	settings = classifier.get_settings()
	model = settings.ai_model or classifier.DEFAULT_MODEL
	system += UNTRUSTED_TEXT_RULE
	reply_rule = (
		"\n\nReply with ONLY one JSON object that follows this JSON schema, with no commentary and no "
		"code fences:\n" + json.dumps(schema)
	)

	if classifier.uses_jarvis(settings):
		reply = classifier.jarvis_complete(system + reply_rule + classifier.JARVIS_REPLY_INSTRUCTIONS, user)
		return classifier.parse_json_reply(reply)

	request = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": user}]}
	if classifier.is_claude(model):
		request["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
		request["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
	else:
		request["system"] = system + reply_rule

	started = time.monotonic()
	response = classifier._client(settings).messages.create(**request)
	classifier._record_usage(None, settings, model, response, int((time.monotonic() - started) * 1000))
	if response.stop_reason in ("refusal", "max_tokens"):
		frappe.throw(_("The AI stopped early: {0}").format(response.stop_reason))
	text = next((b.text for b in response.content if b.type == "text"), None)
	if not text:
		frappe.throw(_("The AI returned no text"))
	return json.loads(text) if classifier.is_claude(model) else classifier.parse_json_reply(text)
