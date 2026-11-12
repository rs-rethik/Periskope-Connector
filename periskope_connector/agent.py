"""The learning agent: a background job that keeps reading new conversations.

Every 15 minutes (scheduler) it takes the messages saved since its last pass, leaving out chats
that are still active (the last hour), and:
1. builds the knowledge base from questions the reps answered (knowledge.py),
2. asks the learner for changes to tags, keyword rules, SLA levels and settings (learner.py),
3. emails the pending suggestions to the managers in one message (suggestions.send_digest).

It writes knowledge entries (applied at once) and suggestions. A change to the configuration needs a
manager's approval. Each pass is a Periskope Agent Run row, which the Health tab and Flow read.
"""

from datetime import timedelta

import frappe
from frappe.utils import add_days, get_datetime, now_datetime

from periskope_connector import knowledge, learner, query, suggestions
from periskope_connector.ai import assistant
from periskope_connector.health import daily_share_used, month_tokens
from periskope_connector.setup_status import agent_on
from periskope_connector.system import insert_as_system

QUIET_MINUTES = 60  # a chat counts as finished when nothing was said for this long
MAX_CHATS = 15
MAX_MESSAGES = 40  # per chat in the prompt
MAX_BODY = 400
LEARN_EVERY_HOURS = 6
LEARN_MIN_MESSAGES = 10
STALE_CHECK_HOURS = 24
FAILURE_PAUSE_MINUTES = 45
FIRST_LOOKBACK_DAYS = 14  # how far back the first pass reads
LOCK = "periskope_agent"
FIELDS = [
	"name",
	"chat_id",
	"direction",
	"body",
	"message_timestamp",
	"is_group",
	"agent_reply",
	"message_type",
]


def _last_cursor():
	return query.latest("Periskope Agent Run", "cursor_to", {"status": ["in", ["Done", "Skipped"]]})


def transcript(chats: dict[str, list]) -> tuple[str, dict[int, list[str]]]:
	"""The prompt text for a set of chats, and the message names behind each numbered conversation."""
	lines, evidence = [], {}
	for number, (_chat_id, messages) in enumerate(chats.items(), start=1):
		kind = "group" if messages[0].is_group else "1-to-1"
		lines.append(f"### Conversation {number} ({kind})")
		for m in messages[-MAX_MESSAGES:]:
			who = (
				"Assistant (automatic)"
				if m.agent_reply
				else ("Rep" if m.direction == "Outgoing" else "Customer")
			)
			text = (m.body or "").strip() or f"[{m.message_type or 'attachment'}]"
			lines.append(f"{who}: {text[:MAX_BODY]}")
		evidence[number] = [m.name for m in messages if m.direction == "Incoming"][-5:] or [messages[-1].name]
		lines.append("")
	return "\n".join(lines), evidence


def _gate(key: str, hours: int) -> bool:
	"""True when `hours` have passed since this was last allowed (kept in the cache)."""
	cache = frappe.cache()
	last = cache.get_value(f"periskope_agent_{key}")
	if last and get_datetime(last) > now_datetime() - timedelta(hours=hours):
		return False
	cache.set_value(f"periskope_agent_{key}", str(now_datetime()))
	return True


def _record(**values):
	return insert_as_system({"doctype": "Periskope Agent Run", "started_on": now_datetime(), **values})


def run() -> dict:
	"""One pass. Scheduler entry point, every 15 minutes."""
	settings = frappe.get_single("Periskope Settings")
	if not agent_on(settings):
		return {"skipped": "the integration is off"}
	if not assistant.is_ready(settings):
		return {"skipped": "no AI key set, or the monthly token budget is used up"}
	if daily_share_used():
		return {"skipped": "today's share of the monthly token budget is used up"}
	failed = frappe.db.get_value(
		"Periskope Agent Run",
		{"status": "Failed", "started_on": [">=", now_datetime() - timedelta(minutes=FAILURE_PAUSE_MINUTES)]},
	)
	if failed:
		return {"skipped": "the last pass failed; trying again later"}
	lock = frappe.cache().lock(LOCK, timeout=1800)
	if not lock.acquire(blocking=False):
		return {"skipped": "another pass is running"}
	try:
		return _pass(settings)
	finally:
		try:
			lock.release()
		except Exception:
			pass


def _pass(settings) -> dict:
	now = now_datetime()
	cutoff = now - timedelta(minutes=QUIET_MINUTES)
	since = _last_cursor() or add_days(now, -FIRST_LOOKBACK_DAYS)
	run_doc = _record(status="Running", cursor_from=since)
	frappe.db.commit()  # so a failed pass can still be recorded after the rollback
	frappe.db.savepoint("periskope_agent_pass")
	tokens_before = month_tokens()
	try:
		rows = frappe.get_all(
			"Periskope Message",
			filters={"message_timestamp": ["between", [since, cutoff]]},
			fields=FIELDS,
			order_by="message_timestamp asc",
		)
		# chats kept away from the AI are not read for learning either
		off = set(frappe.get_all("Periskope Chat", filters={"ai_off": 1}, pluck="chat_id"))
		rows = [
			r
			for r in rows
			if r.chat_id and r.chat_id not in off and ((r.body or "").strip() or r.message_type != "text")
		]
		chats: dict[str, list] = {}
		for row in rows:
			chats.setdefault(row.chat_id, []).append(row)
		cursor_to = cutoff
		if len(chats) > MAX_CHATS:
			ordered = sorted(chats, key=lambda c: chats[c][-1].message_timestamp)[:MAX_CHATS]
			cursor_to = chats[ordered[-1]][-1].message_timestamp
			chats = {c: chats[c] for c in ordered}

		result = {"knowledge": 0, "suggestions": 0, "emails": 0}
		if chats:
			text, evidence = transcript(chats)
			counts = knowledge.mine(text, evidence)
			result["knowledge"] = counts["added"] + counts["changed"]
			if len(rows) >= LEARN_MIN_MESSAGES and _gate("learn", LEARN_EVERY_HOURS):
				result["suggestions"] += learner.analyse(text, evidence)
		if _gate("stale", STALE_CHECK_HOURS):
			result["suggestions"] += knowledge.flag_stale()
		result["suggestions"] += result["knowledge"]
		result["emails"] = 1 if suggestions.send_digest() else 0

		run_doc.db_set(
			{
				"status": "Done",
				"finished_on": now_datetime(),
				"cursor_to": cursor_to,
				"messages_read": len(rows),
				"knowledge_added": result["knowledge"],
				"suggestions_added": result["suggestions"],
				"emails_sent": result["emails"],
				"tokens": max(month_tokens() - tokens_before, 0),
			}
		)
		return {"messages": len(rows), **result}
	except Exception as e:
		frappe.db.rollback(save_point="periskope_agent_pass")
		run_doc.db_set({"status": "Failed", "finished_on": now_datetime(), "error": str(e)[:500]})
		frappe.log_error(title="Periskope: learning agent pass failed")
		raise
