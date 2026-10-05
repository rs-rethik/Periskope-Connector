import frappe

from periskope_connector.timeline_comments import add_comment


# Frappe runs execute() by name from patches.txt on `bench migrate`; do not remove it.
def execute():
	"""Add the timeline comment of the tagged messages that were linked before comments existed."""
	frappe.reload_doc("periskope_connector", "doctype", "periskope_message")
	for name in frappe.get_all(
		"Periskope Message",
		filters={"link_status": ["in", ["Linked", "Ambiguous"]], "timeline_comment": ["is", "not set"]},
		pluck="name",
		order_by="message_timestamp asc",
	):
		add_comment(name)
