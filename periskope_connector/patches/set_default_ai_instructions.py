import frappe

from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS


def execute():
	"""Show the default AI instructions in Periskope Settings so they can be edited."""
	if not (frappe.db.get_single_value("Periskope Settings", "ai_instructions") or "").strip():
		frappe.db.set_single_value("Periskope Settings", "ai_instructions", DEFAULT_INSTRUCTIONS)
