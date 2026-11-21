"""Records the app saved as "Guest" (webhook jobs ran as the guest who called) now belong to the
"Pranera Connect AI" user (ai_user.py). Only the app's own records and the files, comments,
assignments and bells attached to them; other apps' guest records are left as they are.
Idempotent: a second run finds no Guest rows."""

import frappe

from periskope_connector.ai_user import AI_USER, ensure

OWN = ("Periskope Message", "Periskope Chat", "Periskope Contact", "Periskope Webhook Log")
# (doctype, field naming the record it is attached to)
ATTACHED = (("File", "attached_to_doctype"), ("Comment", "reference_doctype"), ("ToDo", "reference_type"))


def execute():
	ensure()
	for doctype in OWN:
		_reassign(doctype)
	for doctype, link_field in ATTACHED:
		_reassign(doctype, link_field)
	_reassign_bells()


def _reassign(doctype: str, link_field: str | None = None):
	table = frappe.qb.DocType(doctype)
	for field in ("owner", "modified_by"):
		query = frappe.qb.update(table).set(table[field], AI_USER).where(table[field] == "Guest")
		if link_field:
			query = query.where(table[link_field].isin(OWN))
		query.run()
	if doctype == "Comment":
		comment = frappe.qb.DocType("Comment")
		(
			frappe.qb.update(comment)
			.set(comment.comment_email, AI_USER)
			.where((comment.comment_email == "Guest") & comment.reference_doctype.isin(OWN))
		).run()
	if doctype == "ToDo":
		todo = frappe.qb.DocType("ToDo")
		(
			frappe.qb.update(todo)
			.set(todo.assigned_by, AI_USER)
			.where((todo.assigned_by == "Guest") & todo.reference_type.isin(OWN))
		).run()


def _reassign_bells():
	"""Bells about the app's chats and messages that say they came from Guest."""
	bell = frappe.qb.DocType("Notification Log")
	for field in ("owner", "from_user"):
		(
			frappe.qb.update(bell)
			.set(bell[field], AI_USER)
			.where((bell[field] == "Guest") & bell.document_type.isin(OWN))
		).run()
