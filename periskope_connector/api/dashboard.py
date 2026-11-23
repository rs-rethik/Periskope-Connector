"""The managers' dashboard of the WhatsApp inbox: work in hand, how fast customers are answered (per
rep, team and number), SLA compliance, message volume, AI tags, leads from WhatsApp and the numbers'
connection, for a date range and optionally one number, team or rep.

Response time: a customer's message that starts a wait (the previous message of the chat was ours, or
there was none) until our first real answer after it (a rep's, or the AI's answer; an AI holding
reply is not an answer). Group chats are left out: the team talks there. A wait counts as within SLA
when it was answered by the chat's SLA policy's reply time (working hours when the policy says so).
"""

import bisect
import json
from collections import defaultdict
from statistics import median

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.caching import redis_cache

from periskope_connector import access, numbers, sla
from periskope_connector.api.permission import require_manager

TOP_TAGS = 8
BREACH_LIMIT = 50
LOOKBACK_DAYS = 30
CACHE_SECONDS = 300  # the dashboard's figures are worked out at most this often


def _range(from_date, to_date):
	to_day = getdate(to_date or nowdate())
	from_day = getdate(from_date or add_days(to_day, -6))
	if from_day > to_day:
		from_day, to_day = to_day, from_day
	return get_datetime(f"{from_day} 00:00:00"), get_datetime(f"{to_day} 23:59:59"), from_day, to_day


def _chat_filter(number=None, team=None, rep=None, table="c") -> tuple[str, dict]:
	conditions, values = [], {}
	if isinstance(number, tuple):  # a manager limited to their numbers (access.allowed_numbers)
		conditions.append(f"{table}.whatsapp_number in %(numbers)s")
		values["numbers"] = number
	elif number:
		conditions.append(f"{table}.whatsapp_number = %(number)s")
		values["number"] = number
	if team:
		conditions.append(f"{table}.team = %(team)s")
		values["team"] = team
	if rep:
		conditions.append(f"{table}._assign like %(rep_like)s")
		values["rep_like"] = access.assigned_like(rep)
	return (" and " + " and ".join(conditions)) if conditions else "", values


@frappe.whitelist()
def get_dashboard(
	from_date: str | None = None,
	to_date: str | None = None,
	number: str | None = None,
	team: str | None = None,
	rep: str | None = None,
	waiting_minutes: int = 30,
) -> dict:
	access.require_inbox_user()
	require_manager()
	if not access.is_manager():
		frappe.throw(_("The dashboard is for managers."), frappe.PermissionError)
	allowed = access.allowed_numbers()
	if allowed is not None:
		# a manager given numbers sees the figures of those numbers only
		if not allowed or (number and number not in allowed):
			frappe.throw(_("You can see the dashboard of your own numbers only."), frappe.PermissionError)
		number = number or tuple(allowed)
	args = (from_date, to_date, number, team, rep, cint(waiting_minutes) or 30)
	# a test changes the data between calls: it always gets fresh figures
	return _figures(*args) if frappe.flags.in_test else _cached(*args)


@redis_cache(ttl=CACHE_SECONDS)
def _cached(from_date, to_date, number, team, rep, waiting_minutes) -> dict:
	"""The dashboard's figures, kept for CACHE_SECONDS: they are heavy to work out (every wait of the
	period), and a manager reopening the page, or another manager, gets the same numbers at once."""
	return _figures(from_date, to_date, number, team, rep, waiting_minutes)


def _figures(from_date, to_date, number, team, rep, waiting_minutes) -> dict:
	start, end, from_day, to_day = _range(from_date, to_date)
	where, values = _chat_filter(number, team, rep)
	values.update(start=start, end=end)
	waits = _waits(start, end, where, values)
	return {
		"range": {"from": str(from_day), "to": str(to_day)},
		"tiles": _tiles(where, values, waits, waiting_minutes),
		"computed_on": str(frappe.utils.now_datetime().replace(microsecond=0)),
		"response": _summary(waits),
		"by_rep": _grouped(waits, "responder"),
		"by_team": _grouped(waits, "team"),
		"by_number": _by_number(where, values, waits),
		"per_day": _per_day(where, values, from_day, to_day),
		"tags": _tags(where, values),
		"leads": _leads(start, end, number, team, rep),
		"leaderboard": _leaderboard(where, values, waits),
		"reps": _reps(),
		"breaches": _breaches(where, values),
		"numbers": [
			{
				"name": n.name,
				"label": n.display_name or n.number,
				"is_ready": n.is_ready,
				"wa_state": n.wa_state,
				"last_sync_on": n.last_sync_on,
				"disconnected_since": n.disconnected_since,
				"is_active": n.is_active,
			}
			for n in numbers.all_numbers()
		],
	}


# --- Waits and answers ---------------------------------------------------------------------------


def _waits(start, end, where: str, values: dict) -> list[dict]:
	"""Every wait a customer started in the range, with when and by whom it was answered."""
	# The message before the first one in the range decides whether that one starts a wait.
	values = {**values, "lookback": add_days(start, -LOOKBACK_DAYS)}
	starts = frappe.db.sql(
		f"""select o.periskope_chat as chat, o.message_timestamp as asked, c.team, c.whatsapp_number,
			c.sla_policy
		from (
			select m.periskope_chat, m.direction, m.message_timestamp,
				lag(m.direction) over (partition by m.periskope_chat order by m.message_timestamp, m.creation) as prev
			from `tabPeriskope Message` m
			where m.is_group = 0 and ifnull(m.agent_reply, '') not in ('Holding', 'Welcome') and m.is_deleted = 0
				and ifnull(m.periskope_chat, '') != ''
				and m.message_timestamp between %(lookback)s and %(end)s
		) o
		join `tabPeriskope Chat` c on c.name = o.periskope_chat
		where o.direction = 'Incoming' and (o.prev is null or o.prev = 'Outgoing')
			and o.message_timestamp between %(start)s and %(end)s {where}""",
		values,
		as_dict=True,
	)
	if not starts:
		return []
	chats = list({w.chat for w in starts})
	answers = defaultdict(list)
	for row in frappe.db.sql(
		"""select periskope_chat, message_timestamp, agent_reply, sent_by, rep
		from `tabPeriskope Message`
		where periskope_chat in %(chats)s and direction = 'Outgoing' and ifnull(agent_reply, '') not in ('Holding', 'Welcome')
			and ifnull(send_status, '') not in ('Failed', 'Not Sent', 'Sending') and message_timestamp >= %(start)s
		order by message_timestamp""",
		{"chats": chats, "start": values["start"]},
		as_dict=True,
	):
		answers[row.periskope_chat].append(row)
	policies = {
		p.name: p
		for p in frappe.get_all(
			"Periskope SLA Policy", fields=["name", "first_response_minutes", "use_working_hours"]
		)
	}
	out = []
	for w in starts:
		replies = answers.get(w.chat, [])
		i = bisect.bisect_left([r.message_timestamp for r in replies], w.asked)
		reply = replies[i] if i < len(replies) else None
		wait = {
			"chat": w.chat,
			"team": w.team or _("No team"),
			"number": w.whatsapp_number,
			"asked": w.asked,
			"answered": reply.message_timestamp if reply else None,
			"seconds": (get_datetime(reply.message_timestamp) - get_datetime(w.asked)).total_seconds()
			if reply
			else None,
			"responder": _responder(reply) if reply else None,
			"within_sla": None,
		}
		policy = policies.get(w.sla_policy)
		if policy and policy.first_response_minutes:
			due = sla.add_minutes(w.asked, policy.first_response_minutes, bool(policy.use_working_hours))
			answered = get_datetime(reply.message_timestamp) if reply else now_datetime()
			if reply or answered > due:
				wait["within_sla"] = answered <= due
		out.append(wait)
	return out


def _responder(reply) -> str:
	if reply.agent_reply == "Answer":
		return "AI assistant"
	return reply.sent_by or reply.rep or _("From the phone")


def _stats(waits: list[dict]) -> dict:
	answered = [w["seconds"] for w in waits if w["seconds"] is not None]
	judged = [w for w in waits if w["within_sla"] is not None]
	return {
		"waits": len(waits),
		"answered": len(answered),
		"avg_seconds": round(sum(answered) / len(answered)) if answered else None,
		"median_seconds": round(median(answered)) if answered else None,
		"sla_percent": round(100 * sum(1 for w in judged if w["within_sla"]) / len(judged))
		if judged
		else None,
		"sla_judged": len(judged),
	}


def _summary(waits):
	return _stats(waits)


def _grouped(waits: list[dict], key: str) -> list[dict]:
	groups = defaultdict(list)
	for w in waits:
		if key == "responder" and not w["responder"]:
			continue
		groups[w[key]].append(w)
	rows = [{"key": k, "label": _label_for(key, k), **_stats(v)} for k, v in groups.items()]
	return sorted(rows, key=lambda r: (r["avg_seconds"] is None, r["avg_seconds"] or 0))


def _label_for(key, value):
	if key == "responder" and value and "@" in value:
		return frappe.utils.get_fullname(value)
	return value


# --- Tiles, volume, tags -------------------------------------------------------------------------


def _count(sql: str, values: dict) -> int:
	return cint(frappe.db.sql(sql, values)[0][0])


def _tiles(where, values, waits, waiting_minutes: int) -> dict:
	chat = "select count(*) from `tabPeriskope Chat` c where 1=1"
	values = {**values, "waiting_before": frappe.utils.add_to_date(now_datetime(), minutes=-waiting_minutes)}
	return {
		"open": _count(f"{chat} and c.status = 'Open' {where}", values),
		"pending": _count(f"{chat} and c.status = 'Pending' {where}", values),
		"unassigned": _count(
			f"{chat} and c.status in ('Open', 'Pending') and ifnull(c._assign, '') in ('', '[]') {where}",
			values,
		),
		"waiting": _count(f"{chat} and c.status = 'Open' and c.waiting_since is not null {where}", values),
		"waiting_long": _count(
			f"{chat} and c.status = 'Open' and c.waiting_since < %(waiting_before)s {where}", values
		),
		"waiting_minutes": waiting_minutes,
		"resolved": _count(f"{chat} and c.resolved_on between %(start)s and %(end)s {where}", values),
		"received": _count(
			f"""select count(*) from `tabPeriskope Message` m join `tabPeriskope Chat` c on c.name = m.periskope_chat
			where m.direction = 'Incoming' and m.message_timestamp between %(start)s and %(end)s {where}""",
			values,
		),
		"sent": _count(
			f"""select count(*) from `tabPeriskope Message` m join `tabPeriskope Chat` c on c.name = m.periskope_chat
			where m.direction = 'Outgoing' and m.message_timestamp between %(start)s and %(end)s {where}""",
			values,
		),
		"breached": _count(
			f"select count(*) from `tabPeriskope Chat` c where {sla.breached_condition('c')} {where}",
			{**values, "sla_now": now_datetime()},
		),
	}


def _per_day(where, values, from_day, to_day) -> list[dict]:
	rows = frappe.db.sql(
		f"""select date(m.message_timestamp) as day, m.direction, count(*) as n
		from `tabPeriskope Message` m join `tabPeriskope Chat` c on c.name = m.periskope_chat
		where m.message_timestamp between %(start)s and %(end)s {where}
		group by date(m.message_timestamp), m.direction""",
		values,
		as_dict=True,
	)
	counts = {(str(r.day), r.direction): r.n for r in rows}
	out, day = [], from_day
	while day <= to_day:
		out.append(
			{
				"day": str(day),
				"received": counts.get((str(day), "Incoming"), 0),
				"sent": counts.get((str(day), "Outgoing"), 0),
			}
		)
		day = add_days(day, 1)
	return out


def _tags(where, values) -> list[dict]:
	rows = frappe.db.sql(
		f"""select m.ai_category as tag, count(distinct m.periskope_chat) as chats
		from `tabPeriskope Message` m join `tabPeriskope Chat` c on c.name = m.periskope_chat
		where m.direction = 'Incoming' and ifnull(m.ai_category, '') != ''
			and m.message_timestamp between %(start)s and %(end)s {where}
		group by m.ai_category order by chats desc""",
		values,
		as_dict=True,
	)
	if len(rows) > TOP_TAGS:
		rest = sum(r.chats for r in rows[TOP_TAGS:])
		rows = [*rows[:TOP_TAGS], frappe._dict(tag=_("Other"), chats=rest)]
	return rows


def _leads(start, end, number, team, rep) -> dict:
	"""Leads made from WhatsApp in the range, and how many became deals. With a number, team or rep
	chosen: the leads of those chats."""
	if not frappe.db.exists("DocType", "CRM Lead"):
		return {"created": 0, "converted": 0}
	source = frappe.db.get_value("CRM Lead Source", {"source_name": "WhatsApp"}) or "WhatsApp"
	extra, values = "", {"start": start, "end": end, "source": source}
	if number or team or rep:
		where, chat_values = _chat_filter(number, team, rep)
		values.update(chat_values)
		extra = f" and l.name in (select c.crm_lead from `tabPeriskope Chat` c where ifnull(c.crm_lead, '') != '' {where})"
	created = _count(
		f"select count(*) from `tabCRM Lead` l where l.source = %(source)s and l.creation between %(start)s and %(end)s{extra}",
		values,
	)
	converted = _count(
		f"""select count(*) from `tabCRM Lead` l where l.source = %(source)s and l.converted = 1
		and l.creation between %(start)s and %(end)s{extra}""",
		values,
	)
	return {
		"created": created,
		"converted": converted,
		"rate": round(100 * converted / created) if created else None,
	}


def _by_number(where, values, waits) -> list[dict]:
	stats = {r["key"]: r for r in _grouped(waits, "number")}
	rows = frappe.db.sql(
		f"""select c.whatsapp_number as number,
			sum(m.direction = 'Incoming') as received, sum(m.direction = 'Outgoing') as sent
		from `tabPeriskope Message` m join `tabPeriskope Chat` c on c.name = m.periskope_chat
		where m.message_timestamp between %(start)s and %(end)s {where}
		group by c.whatsapp_number""",
		values,
		as_dict=True,
	)
	volume = {r.number: r for r in rows}
	waiting = dict(
		frappe.db.sql(
			f"""select c.whatsapp_number, count(*) from `tabPeriskope Chat` c
			where c.status = 'Open' and c.waiting_since is not null {where} group by c.whatsapp_number""",
			values,
		)
	)
	leads = (
		dict(
			frappe.db.sql(
				f"""select c.whatsapp_number, count(distinct c.crm_lead) from `tabPeriskope Chat` c
			join `tabCRM Lead` l on l.name = c.crm_lead
			where l.creation between %(start)s and %(end)s {where} group by c.whatsapp_number""",
				values,
			)
		)
		if frappe.db.exists("DocType", "CRM Lead")
		else {}
	)
	out = []
	for n in numbers.all_numbers():
		if values.get("number") and n.name != values["number"]:
			continue
		v = volume.get(n.name) or {}
		s = stats.get(n.name) or {}
		out.append(
			{
				"number": n.name,
				"label": n.display_name or n.number,
				"is_ready": n.is_ready,
				"wa_state": n.wa_state,
				"received": cint(v.get("received")),
				"sent": cint(v.get("sent")),
				"avg_seconds": s.get("avg_seconds"),
				"sla_percent": s.get("sla_percent"),
				"waiting": cint(waiting.get(n.name)),
				"leads": cint(leads.get(n.name)),
			}
		)
	return out


def _reps() -> list[dict]:
	"""Everyone who works the WhatsApp chats (active agents and team members), for the rep filter."""
	users = set(frappe.get_all("Periskope Agent", filters={"is_active": 1}, pluck="user"))
	users |= set(
		frappe.get_all("Periskope Team Member", filters={"parenttype": "Periskope Team"}, pluck="user")
	)
	users.discard(None)
	if not users:
		return []
	rows = frappe.get_all(
		"User", filters={"name": ["in", list(users)], "enabled": 1}, fields=["name", "full_name"]
	)
	return sorted(
		({"value": r.name, "label": r.full_name or r.name} for r in rows), key=lambda r: r["label"].lower()
	)


def _leaderboard(where, values, waits) -> list[dict]:
	"""Per rep: open chats assigned now, answers in the range and how fast, chats they resolved."""
	open_rows = frappe.db.sql(
		f"select c._assign from `tabPeriskope Chat` c where c.status in ('Open', 'Pending') {where}", values
	)
	load = defaultdict(int)
	for (assigned,) in open_rows:
		for user in json.loads(assigned or "[]"):
			load[user] += 1
	resolved = defaultdict(int)
	for owner, n in frappe.db.sql(
		"""select owner, count(*) from `tabPeriskope Chat Activity`
		where action = 'Status' and details like '%%→ Resolved' and creation between %(start)s and %(end)s
		group by owner""",
		values,
	):
		resolved[owner] += n
	speed = {r["key"]: r for r in _grouped(waits, "responder")}
	users = (set(load) | set(resolved) | {k for k in speed if k and "@" in k}) - {"Administrator", "Guest"}
	rows = []
	for user in users:
		s = speed.get(user) or {}
		rows.append(
			{
				"user": user,
				"name": frappe.utils.get_fullname(user),
				"open": load.get(user, 0),
				"answered": s.get("answered", 0),
				"avg_seconds": s.get("avg_seconds"),
				"sla_percent": s.get("sla_percent"),
				"resolved": resolved.get(user, 0),
			}
		)
	ai = speed.get("AI assistant")
	if ai:
		rows.append(
			{
				"user": "",
				"name": _("AI assistant"),
				"open": 0,
				"answered": ai["answered"],
				"avg_seconds": ai["avg_seconds"],
				"sla_percent": ai["sla_percent"],
				"resolved": 0,
			}
		)
	return sorted(rows, key=lambda r: (-r["answered"], r["name"]))


def _breaches(where, values) -> list[dict]:
	rows = frappe.db.sql(
		f"""select c.name, c.chat_name, c.contact_number, c.whatsapp_number, c._assign, c.team, c.status,
			c.waiting_since, c.response_due, c.opened_on, c.resolution_due, c.resolved_on
		from `tabPeriskope Chat` c where {sla.breached_condition("c")} {where}
		order by least(ifnull(c.response_due, c.resolution_due), ifnull(c.resolution_due, c.response_due)) asc
		limit {BREACH_LIMIT}""",
		{**values, "sla_now": now_datetime()},
		as_dict=True,
	)
	out = []
	for r in rows:
		state = sla.state(r)
		out.append(
			{
				"name": r.name,
				"title": r.chat_name or (f"+{r.contact_number}" if r.contact_number else r.name),
				"number_label": numbers.label(r.whatsapp_number),
				"team": r.team,
				"assigned_to": json.loads(r._assign or "[]"),
				"due": state["due"],
				"waiting_since": r.waiting_since,
			}
		)
	return out
