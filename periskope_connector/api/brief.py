"""The daily brief at the top of the Today page: what matters for the signed-in person today, in a few
short points, the most important first, each opening its chat or record.

The facts come from the app (the person's waiting chats, urgent or unhappy customer messages since
yesterday with their AI tags, missed calls, tasks due, quiet leads and deals, new customers), with the
inbox's access rules: a rep's brief covers their chats, a manager's every chat they may see. The AI
only words them and picks the order; each point names the fact it is about, so its link is the app's
own. Without the AI (no key, budget used up, an error) the same facts make a plain brief.

The brief is written once a day per person, on the first visit to Today, and kept in the cache; the
refresh button writes it again (at most every REFRESH_SECONDS). Customers never see it.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, get_fullname, now_datetime, pretty_date, today

from periskope_connector import access
from periskope_connector.api import today as today_api
from periskope_connector.api.inbox import _where

KEEP_SECONDS = 26 * 3600
REFRESH_SECONDS = 5 * 60
MAX_POINTS = 6
FACTS_PER_KIND = 6
URGENT_HOURS = 24

SYSTEM = """You write the morning brief for a person who works in a company's WhatsApp sales inbox. \
You get facts about their customers as JSON, each with an id. Write at most {max_points} short points \
for today, the most important first: angry or urgent customers and long waits before routine things. \
Each point is one plain sentence of at most 25 words, in English, naming the customer. Group similar \
small facts into one point (for example several price enquiries). Use only the facts given: never \
invent names, numbers or promises. Every point names the id of the one fact it is mostly about in \
"ref" (or "" when it sums up several). When there is nothing to do, write one point saying so."""

SCHEMA = {
	"type": "object",
	"properties": {
		"points": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {"text": {"type": "string"}, "ref": {"type": "string"}},
				"required": ["text", "ref"],
				"additionalProperties": False,
			},
		}
	},
	"required": ["points"],
	"additionalProperties": False,
}


def _key(user: str) -> str:
	return f"periskope:brief:{user}:{today()}"


@frappe.whitelist()
def get_brief(refresh: int | str = 0) -> dict:
	"""Today's brief for the signed-in person: {points, generated_at, by_ai}. Each point is
	{text, chat?, route?, label?}. Written on the first call of the day, then read from the cache."""
	access.require_inbox_user()
	user = frappe.session.user
	cache = frappe.cache()
	kept = cache.get_value(_key(user), expires=True)
	if kept and not (frappe.utils.cint(refresh) and _may_refresh(kept)):
		return kept
	brief = write(user)
	cache.set_value(_key(user), brief, expires_in_sec=KEEP_SECONDS)
	return brief


def _may_refresh(kept: dict) -> bool:
	made = kept.get("generated_at")
	return not made or (now_datetime() - get_datetime(made)).total_seconds() >= REFRESH_SECONDS


def write(user: str) -> dict:
	facts = gather(user)
	points, by_ai = None, False
	if facts["items"]:
		try:
			points = _ai_points(facts)
			by_ai = bool(points)
		except Exception:
			frappe.log_error(title="Periskope: could not write the daily brief")
	if not points:
		points = _plain_points(facts)
	return {"points": points[:MAX_POINTS], "generated_at": str(now_datetime()), "by_ai": by_ai}


# --- Facts ----------------------------------------------------------------------------------------


def _who(row) -> str:
	return row.get("chat_name") or (
		f"+{row.contact_number}" if row.get("contact_number") else _("a customer")
	)


def gather(user: str) -> dict:
	"""The facts for the brief, each {id, kind, ...} with its link (chat or route)."""
	now = now_datetime()
	items = []

	waiting, waiting_total, breached = today_api._waiting()
	for i, row in enumerate(waiting[:FACTS_PER_KIND]):
		items.append(
			{
				"id": f"w{i}",
				"kind": "waiting for our reply",
				"customer": _who(row),
				"waiting_since": pretty_date(row.waiting_since) if row.waiting_since else "",
				"last_message": (row.last_message_preview or "")[:160],
				"chat": row.name,
			}
		)

	for i, row in enumerate(_urgent(now)):
		items.append(
			{
				"id": f"u{i}",
				"kind": "customer message since yesterday",
				"customer": _who(row),
				"tag": row.ai_category or "",
				"severity": row.severity or "",
				"mood": row.sentiment or "",
				"summary": (row.ai_summary or row.body or "")[:160],
				"chat": row.chat,
			}
		)

	for i, row in enumerate(_missed_calls(now)):
		items.append(
			{
				"id": f"m{i}",
				"kind": "missed WhatsApp call",
				"customer": _who(row),
				"when": pretty_date(row.call_time),
				"chat": row.chat,
			}
		)

	tasks, tasks_total, overdue = today_api._tasks(user)
	for i, row in enumerate(tasks[:FACTS_PER_KIND]):
		items.append(
			{
				"id": f"t{i}",
				"kind": "CRM task due" + (" (overdue)" if row.overdue else ""),
				"task": row.title,
				"for": row.reference_title,
				"route": _record_route(row.reference_doctype, row.reference_docname),
			}
		)

	quiet, quiet_total = today_api._quiet(user, now)
	for i, row in enumerate(quiet[:FACTS_PER_KIND]):
		items.append(
			{
				"id": f"q{i}",
				"kind": f"{'lead' if row.doctype == 'CRM Lead' else 'deal'} with no activity",
				"name": row.title or row.name,
				"idle_days": row.idle_days,
				"route": row.route,
			}
		)

	new, new_total = today_api._new_today()
	for i, row in enumerate(new[:FACTS_PER_KIND]):
		items.append({"id": f"n{i}", "kind": "new customer today", "customer": _who(row), "chat": row.name})

	return {
		"person": (get_fullname(user) or user).split(" ")[0],
		"is_manager": access.is_manager(user),
		"today": today(),
		"totals": {
			"waiting": waiting_total,
			"past_sla": breached,
			"tasks_due": tasks_total,
			"tasks_overdue": overdue,
			"quiet": quiet_total,
			"new_today": new_total,
		},
		"items": items,
	}


def _record_route(doctype: str | None, name: str | None) -> str:
	if doctype in ("CRM Deal", "CRM Lead") and name:
		return f"/crm/{'deals' if doctype == 'CRM Deal' else 'leads'}/{name}"
	return "/crm/tasks"


def _urgent(now) -> list:
	"""Customer messages since yesterday that matter: high severity, unhappy, or escalated. One per
	chat (the latest), in the chats the person may see."""
	conditions, values = _where({})
	conditions += [
		"m.direction = 'Incoming'",
		"m.message_timestamp >= %(since)s",
		"(m.severity = 'High' or m.sentiment = 'Negative' or ifnull(m.escalation, '') != '')",
	]
	values["since"] = add_to_date(now, hours=-URGENT_HOURS)
	rows = frappe.db.sql(
		f"""select m.periskope_chat as chat, c.chat_name, c.contact_number, m.ai_category, m.severity,
			m.sentiment, m.ai_summary, m.body, m.message_timestamp
		from `tabPeriskope Message` m join `tabPeriskope Chat` c on c.name = m.periskope_chat
		where {" and ".join(conditions)}
		order by (m.severity = 'High') desc, m.message_timestamp desc limit 50""",
		values,
		as_dict=True,
	)
	seen, out = set(), []
	for row in rows:
		if row.chat not in seen:
			seen.add(row.chat)
			out.append(row)
	return out[:FACTS_PER_KIND]


def _missed_calls(now) -> list:
	"""Missed incoming calls since yesterday that nobody answered with a message or a call since."""
	if not frappe.db.table_exists("Periskope Call"):
		return []
	conditions, values = _where({})
	conditions += [
		"k.direction = 'Incoming'",
		"k.result in ('missed', 'rejected', 'unavailable', 'cancelled', 'abandoned')",
		"k.call_time >= %(since)s",
		"""not exists (select 1 from `tabPeriskope Message` o where o.periskope_chat = c.name
			and o.direction = 'Outgoing' and o.message_timestamp > k.call_time)""",
		"""not exists (select 1 from `tabPeriskope Call` b where b.periskope_chat = c.name
			and b.direction = 'Outgoing' and b.call_time > k.call_time)""",
	]
	values["since"] = add_to_date(now, hours=-URGENT_HOURS)
	return frappe.db.sql(
		f"""select k.periskope_chat as chat, c.chat_name, c.contact_number, k.call_time
		from `tabPeriskope Call` k join `tabPeriskope Chat` c on c.name = k.periskope_chat
		where {" and ".join(conditions)} order by k.call_time desc limit {FACTS_PER_KIND}""",
		values,
		as_dict=True,
	)


# --- Points ---------------------------------------------------------------------------------------


def _link(item: dict | None) -> dict:
	if not item:
		return {}
	if item.get("chat"):
		return {"chat": item["chat"], "label": _("Open chat")}
	if item.get("route"):
		return {"route": item["route"], "label": _("Open in CRM")}
	return {}


def _ai_points(facts: dict) -> list[dict] | None:
	from periskope_connector.ai import assistant

	if not assistant.is_ready():
		return None
	by_id = {i["id"]: i for i in facts["items"]}
	# the links stay here: the AI sees the facts, never the chat names it could get wrong
	shown = {
		**facts,
		"items": [{k: v for k, v in i.items() if k not in ("chat", "route")} for i in facts["items"]],
	}
	reply = assistant.complete_json(
		SYSTEM.format(max_points=MAX_POINTS), frappe.as_json(shown), SCHEMA, max_tokens=1200
	)
	points = []
	for p in (reply or {}).get("points") or []:
		text = (p.get("text") or "").strip()
		if text:
			points.append({"text": text[:300], **_link(by_id.get(p.get("ref")))})
	return points or None


def _plain_points(facts: dict) -> list[dict]:
	"""The brief without the AI: the same facts, most important first."""
	items, totals = facts["items"], facts["totals"]
	points = []
	for item in items:
		if item["id"].startswith("u"):
			tag = f" ({item['tag']})" if item.get("tag") else ""
			points.append({"text": f"{item['customer']}{tag}: {item['summary']}", **_link(item)})
	for item in items:
		if item["id"].startswith("m"):
			points.append(
				{
					"text": _("Missed call from {0}, {1}.").format(item["customer"], item["when"]),
					**_link(item),
				}
			)
	waiting = [i for i in items if i["id"].startswith("w")]
	if waiting:
		first = waiting[0]
		points.append(
			{
				"text": _("{0} chats wait for a reply; the longest is {1} (since {2}).").format(
					totals["waiting"], first["customer"], first["waiting_since"]
				),
				**_link(first),
			}
		)
	if totals["tasks_due"]:
		points.append(
			{
				"text": _("{0} CRM tasks due today ({1} overdue).").format(
					totals["tasks_due"], totals["tasks_overdue"]
				)
			}
		)
	quiet = [i for i in items if i["id"].startswith("q")]
	if quiet:
		points.append(
			{
				"text": _("{0} has had no activity for {1} days.").format(
					quiet[0]["name"], quiet[0]["idle_days"]
				),
				**_link(quiet[0]),
			}
		)
	if totals["new_today"]:
		points.append({"text": _("{0} new customers wrote today.").format(totals["new_today"])})
	return points or [{"text": _("Nothing is waiting for you. A good day to follow up on your leads.")}]
