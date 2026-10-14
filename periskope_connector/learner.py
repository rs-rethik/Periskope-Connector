"""Learn how the app should be set up from what actually happens in the chats.

One AI call looks at recent conversations together with the current tags, keyword rules, SLA
levels and a few settings, and proposes changes. Every proposal is checked in code (it must exist
or be new, be backed by enough different conversations, and touch only what the agent may touch)
and is saved as a Periskope Suggestion. Nothing is applied here.
"""

import json

import frappe
from frappe.utils import add_days, now_datetime

from periskope_connector import suggestions
from periskope_connector.ai import assistant

MAX_SUGGESTIONS = 8
STATS_DAYS = 30

SYSTEM_PROMPT = """You help a sales team keep the setup of their WhatsApp CRM assistant accurate. You see recent customer conversations, the current configuration and some statistics. Suggest a small number of concrete changes where the conversations show the setup is wrong, incomplete or outdated:
- Tag: add a tag for a recurring topic no tag covers (lowercase letters, numbers, underscores), improve a tag description, or switch off a tag that is never used.
- Keyword Rule: add a rule for words that signal something management must see, add words to a rule, or switch off a rule that only produces noise.
- SLA Level: add or adjust an escalation step (wait_minutes, min_severity Low/Medium/High, action one of: Notify Rep, ToDo for Rep, Email Managers, Email Escalation List) when replies are often late or escalations come too early.
- Setting: only one of the listed settings.
Rules: every suggestion must be backed by at least {min_evidence} different conversations (list their numbers in evidence_chats); do not repeat something that already exists; prefer fewer, stronger suggestions; return an empty list when nothing is clearly needed. target is the existing tag name, rule name, SLA level id or setting name (empty for an addition). payload_json is a JSON object string with only the changed fields: Tag {{"tag_name","description","active"}}, Keyword Rule {{"rule_name","keywords","enabled"}}, SLA Level {{"wait_minutes","min_severity","action"}}, Setting {{"value"}}. reason must say what you saw."""

SCHEMA = {
	"type": "object",
	"properties": {
		"suggestions": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"kind": {"type": "string", "enum": ["Tag", "Keyword Rule", "SLA Level", "Setting"]},
					"operation": {"type": "string", "enum": ["Add", "Modify", "Remove"]},
					"target": {"type": "string"},
					"summary": {"type": "string"},
					"reason": {"type": "string"},
					"payload_json": {"type": "string"},
					"evidence_chats": {"type": "array", "items": {"type": "integer"}},
				},
				"required": [
					"kind",
					"operation",
					"target",
					"summary",
					"reason",
					"payload_json",
					"evidence_chats",
				],
				"additionalProperties": False,
			},
		}
	},
	"required": ["suggestions"],
	"additionalProperties": False,
}


def snapshot() -> dict:
	"""The current configuration, as the AI sees it."""
	settings = frappe.get_doc("Periskope Settings")
	since = add_days(now_datetime(), -STATS_DAYS)
	by_tag = frappe.get_all(
		"Periskope Message",
		filters={"ai_category": ["is", "set"], "creation": [">=", since]},
		fields=["ai_category", "count(name) as n"],
		group_by="ai_category",
	)
	corrected = frappe.get_all(
		"Periskope Message",
		filters={"correct_category": ["is", "set"], "creation": [">=", since]},
		fields=["ai_category", "correct_category", "count(name) as n"],
		group_by="ai_category, correct_category",
	)
	alerts = frappe.get_all(
		"Periskope Keyword Alert",
		filters={"creation": [">=", since]},
		fields=["rule", "count(name) as n"],
		group_by="rule",
	)
	return {
		"tags": [
			{"name": t.name, "description": t.description, "active": bool(t.active)}
			for t in frappe.get_all("Periskope Tag", fields=["name", "description", "active"])
		],
		"keyword_rules": [
			{"name": r.rule_name, "keywords": r.keywords, "enabled": bool(r.enabled)}
			for r in frappe.get_all("Periskope Keyword Rule", fields=["rule_name", "keywords", "enabled"])
		],
		"sla_levels": [
			{
				"id": r.name,
				"wait_minutes": r.wait_minutes,
				"min_severity": r.min_severity,
				"action": r.action,
			}
			for r in frappe.get_all(
				"Periskope SLA Level",
				fields=["name", "wait_minutes", "min_severity", "action"],
				order_by="wait_minutes asc",
			)
		],
		"settings": {f: settings.get(f) for f in suggestions.LEARNABLE_SETTINGS},
		"statistics_last_30_days": {
			"messages_per_tag": {r.ai_category: r.n for r in by_tag},
			"human_corrections": [
				{"ai": r.ai_category, "correct": r.correct_category, "count": r.n} for r in corrected
			],
			"keyword_alerts_per_rule": {r.rule: r.n for r in alerts},
			"open_or_escalated_reminders": frappe.db.count(
				"Periskope Reply Reminder", {"status": ["in", ["Open", "Escalated"]]}
			),
		},
	}


def _current(item: dict, config: dict):
	"""The existing record a Modify/Remove points at (None when it does not exist)."""
	kind, target = item["kind"], item["target"]
	pool = {
		"Tag": [(t["name"], t) for t in config["tags"]],
		"Keyword Rule": [(r["name"], r) for r in config["keyword_rules"]],
		"SLA Level": [(r["id"], r) for r in config["sla_levels"]],
		"Setting": [(k, {"value": v}) for k, v in config["settings"].items()],
	}[kind]
	return next((value for key, value in pool if str(key).lower() == target.lower()), None)


def validate(item: dict, config: dict, evidence_ok: bool) -> tuple[bool, object]:
	"""(accepted, current value). The code, not the AI, decides what is acceptable."""
	if not evidence_ok:
		return False, None
	current = _current(item, config) if item["operation"] != "Add" else None
	if item["operation"] != "Add" and current is None:
		return False, None
	if item["operation"] == "Add" and item["kind"] == "Setting":
		return False, None
	if item["operation"] == "Add" and item["kind"] in ("Tag", "Keyword Rule"):
		try:
			name = json.loads(item["payload_json"] or "{}").get(
				"tag_name" if item["kind"] == "Tag" else "rule_name"
			)
		except ValueError:
			return False, None
		exists = _current({**item, "operation": "Modify", "target": name or ""}, config)
		if not name or exists is not None:
			return False, None
	if item["kind"] == "Setting" and item["operation"] == "Remove":
		return False, None
	return True, current


def analyse(transcript: str, chats: dict[int, list[str]]) -> int:
	"""One AI call over a batch of conversations. Returns how many suggestions were saved."""
	min_evidence = frappe.db.get_single_value("Periskope Settings", "learning_min_evidence") or 3
	config = snapshot()
	user = "Configuration:\n" + json.dumps(config, default=str) + "\n\nConversations:\n" + transcript
	result = assistant.complete_json(SYSTEM_PROMPT.format(min_evidence=min_evidence), user, SCHEMA, 3000)

	saved = 0
	for item in (result.get("suggestions") or [])[:MAX_SUGGESTIONS]:
		cited = {n for n in item.get("evidence_chats") or [] if n in chats}
		ok, current = validate(item, config, len(cited) >= int(min_evidence))
		if not ok:
			continue
		try:
			payload = json.loads(item["payload_json"] or "{}")
		except ValueError:
			continue
		if not isinstance(payload, dict):
			continue
		evidence = [m for n in sorted(cited) for m in chats[n][:3]]
		made = suggestions.create(
			item["kind"],
			item["operation"],
			item["summary"],
			reason=item["reason"],
			target=item["target"],
			payload=payload,
			current=current if isinstance(current, dict) else None,
			evidence=evidence,
		)
		saved += 1 if made else 0
	return saved
