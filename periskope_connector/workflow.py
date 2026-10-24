"""The life of a chat in the WhatsApp inbox, like a Helpdesk ticket.

- Status: Open, Pending (waiting on the customer), Resolved, Closed. A customer's message opens a
  Pending, Resolved or Closed chat again.
- A new chat goes to the team of its number (Periskope Team), whose assignment rule gives it to a
  member. With no team (or a Manual one) nobody is assigned by itself: Frappe emails every assignee,
  so automatic assignment is only what a manager set up on a team.
- The SLA clocks (sla.py): a customer's first unanswered message starts the reply clock, our answer
  (a rep's, or the AI's real answer; not an AI holding reply) stops it. Opening the chat starts the
  resolution clock, resolving it stops it.
- Every change is a Periskope Chat Activity row, shown in the chat's activity log.

chat.ensure() calls on_message() for each message that is the newest of its chat; it may run more
than once for the same message (a message is linked again after the AI reads it), so every step here
only acts once.
"""

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from periskope_connector import sla

STATUSES = ("Open", "Pending", "Resolved", "Closed")
DONE = ("Resolved", "Closed")


def log(chat: str, action: str, details: str = "", user: str | None = None):
	"""One line in the chat's activity log. Never raises."""
	try:
		doc = frappe.get_doc(
			{"doctype": "Periskope Chat Activity", "chat": chat, "action": action, "details": details}
		)
		doc.insert(ignore_permissions=True)
		if user:
			frappe.db.set_value("Periskope Chat Activity", doc.name, "owner", user, update_modified=False)
	except Exception:
		frappe.log_error(title="Periskope: could not write the chat activity")


def _row(chat: str) -> frappe._dict:
	return frappe.db.get_value(
		"Periskope Chat",
		chat,
		[
			"name",
			"status",
			"team",
			"whatsapp_number",
			"_assign",
			"waiting_since",
			"response_due",
			"opened_on",
			"resolution_due",
			"first_responded_on",
			"sla_policy",
		],
		as_dict=True,
	)


def _policy(row):
	return sla.policy_for(row.whatsapp_number, row.team)


def new_chat(chat: str, opened_on=None):
	"""A chat row was just made: its team, its rep and its clocks."""
	from periskope_connector.periskope_connector.doctype.periskope_team.periskope_team import team_for_number

	row = _row(chat)
	opened_on = opened_on or now_datetime()
	team = row.team or team_for_number(row.whatsapp_number)
	policy = sla.policy_for(row.whatsapp_number, team)
	frappe.db.set_value(
		"Periskope Chat",
		chat,
		{
			"team": team,
			"opened_on": opened_on,
			"sla_policy": policy.name if policy else None,
			"resolution_due": sla.due(policy, "resolution_minutes", opened_on),
		},
		update_modified=False,
	)
	assign_by_rule(chat)


def assign_by_rule(chat: str):
	"""Give an unassigned chat of a team to a member, by the team's assignment rule."""
	doc = frappe.get_doc("Periskope Chat", chat)
	if not doc.team or json.loads(doc.get("_assign") or "[]"):
		return
	from frappe.automation.doctype.assignment_rule.assignment_rule import apply

	apply(doc)


def on_message(chat: str, message) -> None:
	"""The newest message of a chat was saved (or saved again)."""
	row = _row(chat)
	stamp = get_datetime(message.message_timestamp) if message.message_timestamp else now_datetime()
	if message.direction == "Incoming":
		_customer_wrote(row, stamp)
	elif message.get("agent_reply") != "Holding":
		_we_answered(row, stamp)


def _customer_wrote(row, stamp):
	values = {}
	if row.status != "Open":
		policy = _policy(row)
		values.update(
			status="Open",
			opened_on=stamp,
			resolved_on=None,
			sla_policy=policy.name if policy else None,
			resolution_due=sla.due(policy, "resolution_minutes", stamp),
		)
		log(row.name, "Reopened", _("The customer wrote again (was {0})").format(row.status))
	if not row.waiting_since:
		policy = _policy(row)
		values.update(
			waiting_since=stamp,
			response_due=sla.due(policy, "first_response_minutes", stamp),
			breach_alerted=0,
			sla_policy=policy.name if policy else None,
		)
	if values:
		frappe.db.set_value("Periskope Chat", row.name, values, update_modified=False)
		if values.get("status") == "Open":
			assign_by_rule(row.name)


def _we_answered(row, stamp):
	if not row.waiting_since:
		return
	values = {"waiting_since": None, "response_due": None, "breach_alerted": 0}
	if row.response_due and stamp > get_datetime(row.response_due):
		values["response_breached"] = 1
	if not row.first_responded_on:
		values["first_responded_on"] = stamp
	frappe.db.set_value("Periskope Chat", row.name, values, update_modified=False)


def set_status(chat: str, status: str, user: str | None = None):
	if status not in STATUSES:
		frappe.throw(_("Unknown status {0}").format(status))
	row = _row(chat)
	if row.status == status:
		return
	values = {"status": status}
	now = now_datetime()
	if status in DONE:
		values.update(resolved_on=now, waiting_since=None, response_due=None)
	elif row.status in DONE:
		policy = _policy(row)
		values.update(
			opened_on=now, resolved_on=None, resolution_due=sla.due(policy, "resolution_minutes", now)
		)
	frappe.db.set_value("Periskope Chat", chat, values)
	log(chat, "Status", _("{0} → {1}").format(row.status, status), user=user)


def assign(chat: str, users: list[str], by: str | None = None):
	"""Assign through Frappe (a ToDo per user and the assignment notification), as Helpdesk does."""
	from frappe.desk.form.assign_to import _add

	current = json.loads(frappe.db.get_value("Periskope Chat", chat, "_assign") or "[]")
	new = [u for u in users if u and u not in current]
	if not new:
		return
	_add({"doctype": "Periskope Chat", "name": chat, "assign_to": new}, ignore_permissions=True)
	log(chat, "Assigned", ", ".join(frappe.utils.get_fullname(u) for u in new), user=by)


def unassign(chat: str, user: str, by: str | None = None):
	from frappe.desk.form.assign_to import _remove

	if user in json.loads(frappe.db.get_value("Periskope Chat", chat, "_assign") or "[]"):
		_remove("Periskope Chat", chat, user, ignore_permissions=True)
		log(chat, "Unassigned", frappe.utils.get_fullname(user), user=by)


def set_team(chat: str, team: str | None, user: str | None = None):
	old = frappe.db.get_value("Periskope Chat", chat, "team")
	if old == team:
		return
	frappe.db.set_value("Periskope Chat", chat, "team", team)
	log(chat, "Team", _("{0} → {1}").format(old or _("no team"), team or _("no team")), user=user)
	if team:
		assign_by_rule(chat)
