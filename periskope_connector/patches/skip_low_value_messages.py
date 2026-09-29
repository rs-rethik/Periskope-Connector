import frappe

from periskope_connector.text_rules import analysis_skip_reason


# Frappe runs execute() by name from patches.txt on `bench migrate` (dynamic dispatch),
# so nothing in this app calls it directly. Do not remove it: it would break migrations.
def execute():
	"""Messages still waiting for the AI that are too small to be worth the tokens ('ok', 'done',
	emoji, one word) are marked skipped instead."""
	waiting = frappe.get_all(
		"Periskope Message",
		filters={"message_type": "chat"},
		or_filters=[
			["classification_status", "is", "not set"],
			["classification_status", "in", ["Pending", "Failed"]],
		],
		fields=["name", "body"],
	)
	for row in waiting:
		reason = analysis_skip_reason(row.body)
		if reason:
			frappe.db.set_value(
				"Periskope Message",
				row.name,
				{"classification_status": "Skipped", "classification_error": reason},
				update_modified=False,
			)
