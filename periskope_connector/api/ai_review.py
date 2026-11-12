"""What the AI decided about one customer message, and the managers' corrections: the right tag, an
escalation that was wrong (or missing), a fresh analysis, and taking back a lead the AI should not
have made. The corrections are kept on the message, and the learning agent reads them."""

import frappe
from frappe import _

from periskope_connector import access
from periskope_connector.api.permission import require_manager

FIELDS = [
	"name",
	"periskope_chat",
	"direction",
	"classification_status",
	"classification_error",
	"ai_category",
	"correct_category",
	"severity",
	"sentiment",
	"ai_summary",
	"ai_confidence",
	"needs_reply",
	"escalation",
	"escalation_reason",
	"escalation_feedback",
	"ai_feedback_note",
	"ai_lead",
	"classified_at",
]


def _manager():
	require_manager()
	if not access.is_manager():  # frappe.only_for is skipped in tests: the roles themselves
		frappe.throw(_("Only a manager can correct the AI."), frappe.PermissionError)


def _message(message: str) -> frappe._dict:
	access.require_inbox_user()
	row = frappe.db.get_value("Periskope Message", message, FIELDS, as_dict=True)
	if not row or not row.periskope_chat or not access.can_see_chat(row.periskope_chat):
		frappe.throw(_("You cannot open this message."), frappe.PermissionError)
	return row


@frappe.whitelist(methods=["GET", "POST"])
def details(message: str) -> dict:
	"""What the AI made of the message (for anyone who may open the chat), and whether the user may
	correct it."""
	access.require_inbox_user()
	row = _message(message)
	from periskope_connector.tags import names

	return {
		**row,
		"can_correct": access.is_manager(),
		"tags": names() if access.is_manager() else [],
		"lead_exists": bool(row.ai_lead and frappe.db.exists("CRM Lead", row.ai_lead)),
	}


def _feedback(message: str, values: dict, note: str = ""):
	values.update(ai_feedback_by=frappe.session.user)
	if note.strip():
		values["ai_feedback_note"] = note.strip()[:500]
	frappe.db.set_value("Periskope Message", message, values, update_modified=False)


@frappe.whitelist(methods=["POST"])
def correct(message: str, category: str | None = None, escalation: str | None = None, note: str = "") -> dict:
	"""A manager's correction: the tag the AI should have given, and/or whether its escalation (or its
	choice not to escalate) was Right or Wrong."""
	access.require_inbox_user()
	access.require_inbox_user()
	_manager()
	_message(message)
	values = {}
	if category:
		from periskope_connector.tags import names

		if category not in names():
			frappe.throw(_("Category must be one of: {0}").format(", ".join(names())))
		values["correct_category"] = category
	if escalation:
		if escalation not in ("Right", "Wrong"):
			frappe.throw(_("Say whether the escalation was Right or Wrong."))
		values["escalation_feedback"] = escalation
	if not values and not note.strip():
		frappe.throw(_("Nothing to correct."))
	_feedback(message, values, note)
	return details(message)


@frappe.whitelist(methods=["POST"])
def analyse_again(message: str) -> None:
	"""Managers: send the message to the AI again (its tag, escalation and tasks are decided anew)."""
	access.require_inbox_user()
	access.require_inbox_user()
	_manager()
	row = _message(message)
	if row.direction != "Incoming":
		frappe.throw(_("Only a customer's message is analysed."))
	from periskope_connector.ai import classifier

	frappe.db.set_value(
		"Periskope Message",
		message,
		{"classification_status": "Pending", "classification_attempts": 0, "classification_error": ""},
		update_modified=False,
	)
	classifier.queue(message)


@frappe.whitelist(methods=["POST"])
def remove_ai_lead(message: str, note: str = "") -> None:
	"""Managers: delete a lead the AI made from this message by mistake (not a business enquiry). Only
	while nothing was built on it: no deal, not converted. The number's messages are unlinked and its
	contact-queue row goes back to New."""
	access.require_inbox_user()
	access.require_inbox_user()
	_manager()
	row = _message(message)
	lead = row.ai_lead
	if not lead or not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("The AI made no lead from this message, or it is already gone."))
	if frappe.db.get_value("CRM Lead", lead, "converted") or frappe.db.exists("CRM Deal", {"lead": lead}):
		frappe.throw(_("Lead {0} already has a deal: change it in the CRM instead.").format(lead))
	frappe.has_permission("CRM Lead", "delete", lead, throw=True)
	number = frappe.db.get_value("CRM Lead", lead, "periskope_phone_e164")
	chats = frappe.get_all("Periskope Chat", filters={"crm_lead": lead}, pluck="chat_id", distinct=True)
	for name in frappe.get_all("Periskope Message", filters={"ai_lead": lead}, pluck="name"):
		frappe.db.set_value("Periskope Message", name, "ai_lead", None, update_modified=False)
	for name in frappe.get_all("Periskope Message", filters={"crm_lead": lead}, pluck="name"):
		frappe.db.set_value(
			"Periskope Message", name, {"crm_lead": None, "link_status": ""}, update_modified=False
		)
	from periskope_connector import chat
	from periskope_connector.linking import relink_messages

	for chat_id in chats:
		chat.set_on_all(chat_id, {"crm_lead": None, "linked_by_hand": 0, "link_review": 0})
	if number:
		frappe.db.set_value(
			"Periskope Contact",
			{"phone": number, "linked_lead": lead},
			{"status": "New", "linked_lead": None},
		)
	# The notes and tasks the agent wrote on the lead go with it.
	for rec in frappe.get_all(
		"Periskope CRM Record",
		filters={"reference_doctype": "CRM Lead", "reference_name": lead},
		fields=["name", "target_doctype", "target_name"],
	):
		if rec.target_doctype in ("FCRM Note", "CRM Task") and frappe.db.exists(
			rec.target_doctype, rec.target_name
		):
			frappe.delete_doc(rec.target_doctype, rec.target_name, ignore_permissions=True)
		frappe.delete_doc("Periskope CRM Record", rec.name, ignore_permissions=True)
	frappe.delete_doc("CRM Lead", lead)
	for chat_id in chats:
		relink_messages(chat_id)
	_feedback(message, {}, note or _("The AI should not have made lead {0}").format(lead))
