import frappe

from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS
from periskope_connector.text_rules import NO_CATEGORY_SKIPS

# The text the previous default started its category list with. Instructions that still contain it
# were never edited, so they can be replaced by the new default.
PREVIOUS_DEFAULT_MARK = "quality_complaint: the customer complains about product quality"


# Frappe runs execute() by name from patches.txt on `bench migrate`; do not remove it.
def execute():
	"""Align with the spec: only customer messages are classified, skipped ones count as
	general / low, three earlier messages more of context, and the current default instructions."""
	frappe.reload_doc("periskope_connector", "doctype", "periskope_message")

	# Rep messages are never classified.
	frappe.db.sql(
		"""update `tabPeriskope Message`
		set classification_status = 'Skipped', classification_error = 'Sent by the rep',
			ai_category = null, severity = null
		where direction = 'Outgoing' and ifnull(classification_status, '') in ('', 'Pending', 'Failed')"""
	)
	# A skipped customer message counts as general with low severity.
	frappe.db.sql(
		"""update `tabPeriskope Message` set ai_category = 'general', severity = 'Low'
		where direction = 'Incoming' and classification_status = 'Skipped' and ifnull(ai_category, '') = ''
			and ifnull(classification_error, '') not in %s""",
		(tuple(NO_CATEGORY_SKIPS),),
	)

	# The spec gives the AI the last 5 messages of the chat.
	if not frappe.db.get_single_value("Periskope Settings", "context_messages"):
		frappe.db.set_single_value("Periskope Settings", "context_messages", 5)

	current = frappe.db.get_single_value("Periskope Settings", "ai_instructions") or ""
	if not current.strip() or PREVIOUS_DEFAULT_MARK in current:
		frappe.db.set_single_value("Periskope Settings", "ai_instructions", DEFAULT_INSTRUCTIONS)
