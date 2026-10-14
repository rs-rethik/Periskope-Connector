"""What happens when a message gets a tag. The rules are rows on the Periskope Tag (Actions table),
so the team decides, not the code. Each row names an action, the lowest severity it runs at, and
whether it applies to 1-to-1 chats, groups or both.

Actions:
- Create Lead: a customer message from an unknown 1-to-1 number becomes a CRM Lead (also needs
  "Create Leads from New Enquiries" on in the settings, and all the safety checks in leads.py).
- Email Alert: email the recipients on the row (or the Manager Emails when empty), once per message.
- Timeline Comment: show the message on its lead or deal timeline.
- ToDo for Rep: a ToDo for the rep that owns the phone, once per message.
"""

import frappe
from frappe import _
from frappe.utils import get_url_to_form

CREATE_LEAD = "Create Lead"
EMAIL_ALERT = "Email Alert"
TIMELINE_COMMENT = "Timeline Comment"
TODO_FOR_REP = "ToDo for Rep"

# The message fields each step reads. The message is only read here, so no document is built.
TAG_FIELDS = ["ai_category", "severity", "is_group"]
TODO_FIELDS = ["rep", "severity", "ai_summary", "body", "ai_category", "chat_id"]

SEVERITY_RANK = {"Low": 0, "Medium": 1, "High": 2}
ONE_TO_ONE, GROUP = "1-to-1", "Group"


def matching(doc, action: str) -> list[dict]:
	"""The rows of the message's tag that say to run `action` for this message."""
	if not doc.ai_category:
		return []
	rows = frappe.get_all(
		"Periskope Tag Action",
		filters={"parent": doc.ai_category, "parenttype": "Periskope Tag", "action": action},
		fields=["min_severity", "chat_type", "recipients"],
		order_by="idx asc",
	)
	severity = SEVERITY_RANK.get(doc.severity or "Low", 0)
	matched = []
	for row in rows:
		if severity < SEVERITY_RANK.get(row.min_severity or "Low", 0):
			continue
		if row.chat_type == GROUP and not doc.is_group:
			continue
		if row.chat_type == ONE_TO_ONE and doc.is_group:
			continue
		matched.append(row)
	return matched


def run(message: str):
	"""Run the actions of the message's tag, after the AI has tagged it. Each action is guarded: one
	failing never stops the others or undoes the tags. (Timeline Comment runs when the message is
	linked, see linking.link_message.)"""
	from periskope_connector.alerts import send_alert
	from periskope_connector.leads import create_lead_for_message

	doc = frappe.db.get_value("Periskope Message", message, TAG_FIELDS, as_dict=True)
	if not doc:
		return
	if matching(doc, CREATE_LEAD):
		create_lead_for_message(message)
	rows = matching(doc, EMAIL_ALERT)
	if rows:
		send_alert(message, rows)
	if matching(doc, TODO_FOR_REP):
		create_rep_todo(message)


def create_rep_todo(message: str) -> str | None:
	"""A task for the rep to look at this message: a CRM Task on the lead or deal (one per chat and
	tag, updated while open), else a desk ToDo (one per message). Never raises."""
	try:
		doc = frappe.db.get_value("Periskope Message", message, TODO_FIELDS, as_dict=True)
		if doc and doc.chat_id:
			from periskope_connector import crm_context, writeback

			if crm_context.record_of(doc.chat_id):
				return writeback.upsert_task(
					doc.chat_id,
					f"tag:{doc.ai_category}",
					doc.ai_summary or (doc.body or "")[:100],
					_("WhatsApp message tagged {0}: {1}").format(doc.ai_category, (doc.body or "")[:500]),
					priority="High" if doc.severity == "High" else "Medium",
				)
		if not doc or not doc.rep or not frappe.db.get_value("User", doc.rep, "enabled"):
			return None
		if frappe.db.exists(
			"ToDo",
			{"reference_type": "Periskope Message", "reference_name": message, "allocated_to": doc.rep},
		):
			return None
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": doc.rep,
				"reference_type": "Periskope Message",
				"reference_name": message,
				"priority": "High" if doc.severity == "High" else "Medium",
				"description": _("WhatsApp: {0} ({1})").format(
					doc.ai_summary or (doc.body or "")[:100], doc.ai_category
				)
				+ f" <a href='{get_url_to_form('Periskope Message', message)}'>{_('Open')}</a>",
			}
		)
		todo.insert(ignore_permissions=True)
		return todo.name
	except Exception:
		frappe.log_error(title=f"Periskope: could not create the ToDo for message {message}")
		return None
