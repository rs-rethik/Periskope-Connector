"""Periskope Settings offered webhook events the app never processed (chat.created, ticket.*,
message.flagged, ...): they are removed from the selection, and the number connect/disconnect events,
which the app now handles, are added. Test Connection registers the new ones in Periskope."""

import frappe

from periskope_connector.messages import HANDLED_EVENTS

ADD = ("org.phone.connected", "org.phone.disconnected")


def execute():
	if not frappe.db.exists("DocType", "Periskope Webhook Event"):
		return
	event = frappe.qb.DocType("Periskope Webhook Event")
	frappe.qb.from_(event).delete().where(
		(event.parent == "Periskope Settings") & event.event.notin(list(HANDLED_EVENTS))
	).run()
	chosen = set(
		frappe.get_all("Periskope Webhook Event", filters={"parent": "Periskope Settings"}, pluck="event")
	)
	if not chosen:
		return  # never set up: the install picks the defaults
	# Added as rows, not by saving the settings: their validation must not stop a migrate.
	idx = len(chosen)
	for name in ADD:
		if name in chosen:
			continue
		idx += 1
		frappe.get_doc(
			{
				"doctype": "Periskope Webhook Event",
				"parent": "Periskope Settings",
				"parenttype": "Periskope Settings",
				"parentfield": "webhook_events",
				"event": name,
				"idx": idx,
			}
		).db_insert()
