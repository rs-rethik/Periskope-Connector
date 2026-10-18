"""After a conversation moves on, one AI call per chat keeps the CRM record current: the WhatsApp
summary note, notes for what the customer told us, a follow-up task when the rep must act, a status
step and the customer's company. The AI only proposes; writeback.py checks and writes (test mode
included). At most once per chat every THROTTLE_MINUTES."""

import frappe
from frappe.utils import add_to_date, now_datetime

from periskope_connector import crm_context, safety, writeback
from periskope_connector.ai import assistant

THROTTLE_MINUTES = 15
MAX_MESSAGES = 30
MAX_NOTES = 4

SYSTEM_PROMPT = """You keep a sales CRM record up to date from a WhatsApp conversation between a customer and a company (its reps and an automatic assistant). You get the conversation, what the CRM already knows (internal), and the record's possible statuses. Write for the sales rep, in plain English sentences, short and factual. Never include phone numbers, ids, confidence scores or raw data.
- summary.wants: what the customer wants. summary.answered: what has been answered so far, by a rep or the assistant. summary.pending: what is still open. One to three sentences each; "Nothing" when empty.
- notes: only important, lasting facts the customer shared (requirements, quantities, delivery, payment terms, contact person, complaint). Leave it empty when there is nothing new beyond the summary.
- follow_up: needed=true only when the rep must do something (call back, send a quote, samples, resolve a complaint). title like "Customer asked for bulk pricing on X - call back today", reason one sentence, due_in_hours from 1 to 72, priority High for complaints, risk of losing the order or a deadline today.
- status: change=true only when the conversation clearly shows the record has moved (for example a quote was requested, an order confirmed, a deal lost). "to" must be one of the statuses listed.
- organization: the customer's company name when it is clearly stated, else an empty string."""

SCHEMA = {
	"type": "object",
	"properties": {
		"summary": {
			"type": "object",
			"properties": {
				"wants": {"type": "string"},
				"answered": {"type": "string"},
				"pending": {"type": "string"},
			},
			"required": ["wants", "answered", "pending"],
			"additionalProperties": False,
		},
		"notes": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"topic": {"type": "string", "enum": list(writeback.TOPICS)},
					"text": {"type": "string"},
				},
				"required": ["topic", "text"],
				"additionalProperties": False,
			},
		},
		"follow_up": {
			"type": "object",
			"properties": {
				"needed": {"type": "boolean"},
				"title": {"type": "string"},
				"reason": {"type": "string"},
				"due_in_hours": {"type": "integer"},
				"priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
			},
			"required": ["needed", "title", "reason", "due_in_hours", "priority"],
			"additionalProperties": False,
		},
		"status": {
			"type": "object",
			"properties": {
				"change": {"type": "boolean"},
				"to": {"type": "string"},
				"reason": {"type": "string"},
			},
			"required": ["change", "to", "reason"],
			"additionalProperties": False,
		},
		"organization": {"type": "string"},
	},
	"required": ["summary", "notes", "follow_up", "status", "organization"],
	"additionalProperties": False,
}


def _transcript(chat_id: str) -> str:
	rows = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": chat_id},
		fields=["direction", "body", "message_type", "agent_reply", "message_timestamp"],
		order_by="message_timestamp desc",
		limit=MAX_MESSAGES,
	)
	lines = []
	for m in reversed(rows):
		who = "Assistant" if m.agent_reply else ("Rep" if m.direction == "Outgoing" else "Customer")
		text = (m.body or "").strip() or f"[{m.message_type or 'attachment'}]"
		lines.append(f"{m.message_timestamp:%d %b %H:%M} {who}: {text[:500]}")
	return "\n".join(lines)


def _statuses(doctype: str) -> list[str]:
	status_doctype = "CRM Deal Status" if doctype == "CRM Deal" else "CRM Lead Status"
	return frappe.get_all(status_doctype, order_by="position asc", pluck="name")


def queue(chat_id: str):
	"""Ask for an analysis soon (one queued job per chat)."""
	if not chat_id:
		return
	frappe.enqueue(
		"periskope_connector.ai.analyse.run",
		chat_id=chat_id,
		queue="short",
		job_id=f"periskope_analyse::{chat_id}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def run(chat_id: str, force: bool = False) -> dict:
	"""Analyse one chat and write the result into its CRM record. Returns what was done."""
	context = crm_context.build(chat_id)
	if not context and not safety.is_group(chat_id):
		return {"skipped": "no CRM record"}
	cache = frappe.cache()
	key = f"periskope_analysed:{chat_id}"
	if not force and cache.get_value(key):
		return {"skipped": "analysed recently"}
	if not assistant.is_ready():
		return {"skipped": "AI not ready (no key, or the monthly budget is used up)"}
	cache.set_value(key, 1, expires_in_sec=THROTTLE_MINUTES * 60)

	statuses = _statuses(context.doctype) if context else []
	user = (
		f"CRM (internal):\n{crm_context.internal_text(context)}\n\n"
		f"Possible statuses: {', '.join(statuses) or 'none'}\n\n"
		f"Conversation:\n{_transcript(chat_id)}"
	)
	result = assistant.complete_json(SYSTEM_PROMPT, user, SCHEMA, 1200)
	done = {"summary": None, "notes": 0, "task": None, "status": "", "organization": None}
	if context:
		s = result.get("summary") or {}
		done["summary"] = writeback.summary(
			chat_id, s.get("wants", ""), s.get("answered", ""), s.get("pending", "")
		)
		for note in (result.get("notes") or [])[:MAX_NOTES]:
			if writeback.upsert_note(chat_id, note.get("topic"), note.get("text")):
				done["notes"] += 1
		follow = result.get("follow_up") or {}
		if follow.get("needed") and (follow.get("title") or "").strip():
			hours = min(max(int(follow.get("due_in_hours") or 4), 1), 72)
			done["task"] = writeback.upsert_task(
				chat_id,
				"follow_up",
				follow["title"],
				follow.get("reason", ""),
				due=add_to_date(now_datetime(), hours=hours),
				priority=follow.get("priority")
				if follow.get("priority") in ("Low", "Medium", "High")
				else "Medium",
			)
		status = result.get("status") or {}
		if status.get("change") and status.get("to") in statuses:
			done["status"] = writeback.set_status(chat_id, status["to"], status.get("reason", ""))
	if (result.get("organization") or "").strip():
		done["organization"] = writeback.link_organization(chat_id, result["organization"])
	return done
