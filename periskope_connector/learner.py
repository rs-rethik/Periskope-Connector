"""Learn how the app should be set up from what actually happens in the chats.

One AI call looks at recent conversations together with the current tags, the unmapped groups, the
unknown numbers and a few settings, and proposes changes. Every proposal is checked in code (it must exist
or be new, be backed by enough different conversations, and touch only what the agent may touch)
and is saved as a Periskope Suggestion. Nothing is applied here.
"""

import json

import frappe
from frappe.utils import add_days, now_datetime

from periskope_connector import chat_tasks, linking, query, suggestions
from periskope_connector.ai import assistant

MAX_SUGGESTIONS = 8
# One group or one number is a single conversation: these are backed by that conversation itself.
OWN_CHAT_KINDS = ("Group Mapping", "Ignore Contact")
STATS_DAYS = 30
MIN_EVIDENCE = 3  # different conversations behind a suggestion

SYSTEM_PROMPT = """You help a sales team keep the setup of their WhatsApp CRM assistant accurate. You see recent customer conversations, the current configuration and some statistics. Suggest a small number of concrete changes where the conversations show the setup is wrong, incomplete or outdated:
- Tag: add a tag for a recurring topic no tag covers (lowercase letters, numbers, underscores), improve a tag description, or switch off a tag that is never used.
- Setting: only one of the listed settings.
- Group Mapping: a group listed under unmapped_groups that clearly belongs to one customer company: target is its chat_id, payload sets group_type "Customer" and organization, which must be one of that group's candidate_organizations written exactly as listed (members' companies come first); leave the group out when none of them fits.
- Ignore Contact: a number listed under unmatched_contacts that is clearly personal, a vendor, spam or a colleague: target is the number, payload status one of "Ignored (Personal)", "Ignored (Vendor)", "Ignored (Spam)", "Ignored (Internal)".
Rules: every suggestion must be backed by at least {min_evidence} different conversations (list their numbers in evidence_chats), except Group Mapping and Ignore Contact, which are backed by that group's or number's own conversation (cite it); do not repeat something that already exists or is listed in pending_suggestions (those wait for a decision); prefer fewer, stronger suggestions; return an empty list when nothing is clearly needed. target is the existing tag name or setting name (empty for an addition). payload_json is a JSON object string with only the changed fields: Tag {{"tag_name","description","active"}}, Setting {{"value"}}, Group Mapping {{"group_type","organization"}}, Ignore Contact {{"status"}}. reason must say what you saw."""

SCHEMA = {
	"type": "object",
	"properties": {
		"suggestions": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"kind": {
						"type": "string",
						"enum": ["Tag", "Setting", "Group Mapping", "Ignore Contact"],
					},
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


def candidate_organizations(group) -> list[str]:
	"""The organizations a group may belong to: its members' companies (by their numbers on leads,
	deals and contacts), then the ones named most like the group. The AI picks only from these, so it
	never invents a company or misses one that is not among the first few hundred."""
	members = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": group.chat_id, "direction": "Incoming", "sender_phone": ["is", "set"]},
		pluck="sender_phone",
		distinct=True,
		limit=10,
	)
	found = []
	for number in members:
		found += sorted(linking.member_organizations(number))
	found += chat_tasks.closest_organizations(group.group_name)
	return list(dict.fromkeys(found))[:8]


def snapshot() -> dict:
	"""The current configuration, as the AI sees it."""
	settings = frappe.get_doc("Periskope Settings")
	since = add_days(now_datetime(), -STATS_DAYS)
	by_tag = query.counts(
		"Periskope Message", ["ai_category"], {"ai_category": ["is", "set"], "creation": [">=", since]}
	)
	corrected = query.counts(
		"Periskope Message",
		["ai_category", "correct_category"],
		{"correct_category": ["is", "set"], "creation": [">=", since]},
	)
	return {
		"tags": [
			{"name": t.name, "description": t.description, "active": bool(t.active)}
			for t in frappe.get_all("Periskope Tag", fields=["name", "description", "active"])
		],
		"settings": {f: settings.get(f) for f in suggestions.LEARNABLE_SETTINGS},
		"unmapped_groups": [
			{
				"chat_id": g.chat_id,
				"name": g.group_name,
				"type": g.group_type,
				"candidate_organizations": candidate_organizations(g),
			}
			for g in frappe.get_all(
				"Periskope Group",
				filters={
					"crm_deal": ["is", "not set"],
					"crm_lead": ["is", "not set"],
					"organization": ["is", "not set"],
				},
				fields=["chat_id", "group_name", "group_type"],
				order_by="modified desc",
				limit=30,
			)
		],
		"unmatched_contacts": [
			{"number": c.phone, "name": c.display_name, "messages": c.message_count, "topic": c.last_category}
			for c in frappe.get_all(
				"Periskope Contact",
				filters={"status": "New"},
				fields=["phone", "display_name", "message_count", "last_category"],
				order_by="message_count desc",
				limit=30,
			)
		],
		"pending_suggestions": [
			f"{p.kind} {p.operation} {p.target or ''}: {p.summary}"
			for p in frappe.get_all(
				"Periskope Suggestion",
				filters={"status": "Pending", "kind": ["!=", "Knowledge Entry"]},
				fields=["kind", "operation", "target", "summary"],
				limit=50,
			)
		],
		"statistics_last_30_days": {
			"messages_per_tag": {r.ai_category: r.n for r in by_tag},
			"human_corrections": [
				{"ai": r.ai_category, "correct": r.correct_category, "count": r.n} for r in corrected
			],
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
		"Setting": [(k, {"value": v}) for k, v in config["settings"].items()],
		"Group Mapping": [(g["chat_id"], g) for g in config.get("unmapped_groups", [])],
		"Ignore Contact": [(c["number"], c) for c in config.get("unmatched_contacts", [])],
	}[kind]
	return next((value for key, value in pool if str(key).lower() == target.lower()), None)


def validate(item: dict, config: dict, evidence_ok: bool) -> tuple[bool, object]:
	"""(accepted, current value). The code, not the AI, decides what is acceptable."""
	if not evidence_ok:
		return False, None
	if item["kind"] in ("Group Mapping", "Ignore Contact"):
		# These always point at something that exists: a listed group, a listed number.
		current = _current(item, config)
		if current is None:
			return False, None
		if item["kind"] == "Ignore Contact" and item["operation"] != "Add":
			return False, None
		if item["kind"] == "Group Mapping":
			return _group_mapping_ok(item, current), _without_candidates(current)
		return True, current
	current = _current(item, config) if item["operation"] != "Add" else None
	if item["operation"] != "Add" and current is None:
		return False, None
	if item["operation"] == "Add" and item["kind"] == "Setting":
		return False, None
	if item["operation"] == "Add" and item["kind"] == "Tag":
		try:
			name = json.loads(item["payload_json"] or "{}").get("tag_name")
		except ValueError:
			return False, None
		exists = _current({**item, "operation": "Modify", "target": name or ""}, config)
		if not name or exists is not None:
			return False, None
	if item["kind"] == "Setting" and item["operation"] == "Remove":
		return False, None
	return True, current


def _group_mapping_ok(item: dict, group: dict) -> bool:
	"""A group goes only to a company the code offered for it, never to a name the AI made up."""
	try:
		payload = json.loads(item.get("payload_json") or "{}")
	except ValueError:
		return False
	if not isinstance(payload, dict) or payload.get("group_type", "Customer") not in ("Customer", "Internal"):
		return False
	organization = payload.get("organization")
	return not organization or organization in group.get("candidate_organizations", [])


def _without_candidates(group: dict) -> dict:
	return {k: v for k, v in group.items() if k != "candidate_organizations"}


def _is_own_chat(item: dict, messages: list[str]) -> bool:
	"""Whether a cited conversation is the target group's or number's own chat."""
	chat_id = frappe.db.get_value("Periskope Message", messages[0], "chat_id") if messages else ""
	target = (item.get("target") or "").strip()
	if not chat_id or not target:
		return False
	if item["kind"] == "Group Mapping":
		return chat_id == target
	return chat_id.split("@")[0] == "".join(c for c in target if c.isdigit())


def analyse(transcript: str, chats: dict[int, list[str]]) -> int:
	"""One AI call over a batch of conversations. Returns how many suggestions were saved."""
	min_evidence = MIN_EVIDENCE
	config = snapshot()
	user = "Configuration:\n" + json.dumps(config, default=str) + "\n\nConversations:\n" + transcript
	result = assistant.complete_json(SYSTEM_PROMPT.format(min_evidence=min_evidence), user, SCHEMA, 3000)

	saved = 0
	for item in (result.get("suggestions") or [])[:MAX_SUGGESTIONS]:
		cited = {n for n in item.get("evidence_chats") or [] if n in chats}
		if item["kind"] in OWN_CHAT_KINDS:
			enough = any(_is_own_chat(item, chats[n]) for n in cited)
		else:
			enough = len(cited) >= int(min_evidence)
		ok, current = validate(item, config, enough)
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
