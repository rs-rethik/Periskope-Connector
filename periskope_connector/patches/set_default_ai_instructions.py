import frappe

from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS


# Frappe runs execute() by name from patches.txt on `bench migrate` (dynamic dispatch),
# so nothing in this app calls it directly. Do not remove it: it would break migrations.
def execute():
	"""Show the default AI instructions in Periskope Settings so they can be edited."""
	if not (frappe.db.get_single_value("Periskope Settings", "ai_instructions") or "").strip():
		frappe.db.set_single_value("Periskope Settings", "ai_instructions", DEFAULT_INSTRUCTIONS)
