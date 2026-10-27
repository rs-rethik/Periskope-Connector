"""Videos, shared locations, contact cards and polls were all saved as message type "other". Give
them their own types (the raw Periskope type is kept on every message) and keep what a location,
contact or poll holds on the message, so the inbox shows them and video files can be opened and
attached like other media."""

import json

import frappe

from periskope_connector import chat, shared_items
from periskope_connector.messages import normalise_type

RAW_TYPES = ("video", "location", "vcard", "multi_vcard", "poll_creation")


def execute():
	rows = frappe.get_all(
		"Periskope Message",
		filters={"message_type": "other", "periskope_type": ["in", RAW_TYPES]},
		fields=["name", "periskope_type", "periskope_chat", "raw_payload"],
	)
	for row in rows:
		kind = normalise_type(row.periskope_type)
		found = shared_items.details(kind, row.raw_payload)
		frappe.db.set_value(
			"Periskope Message",
			row.name,
			{"message_type": kind, "shared_details": json.dumps(found) if found else None},
			update_modified=False,
		)
	for name in {row.periskope_chat for row in rows if row.periskope_chat}:
		chat.refresh(name)  # the list line of a chat whose last message is one of these
