import frappe


def execute():
	"""Give the new contact queue its history: one row per unknown 1-to-1 number already in the
	messages, with counts and names, so the queue is useful the moment the site is upgraded."""
	from periskope_connector.contacts import backfill_contacts

	backfill_contacts()
