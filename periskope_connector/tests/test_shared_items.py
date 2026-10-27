"""Tests for shared locations, contact cards and polls: what the inbox shows of them."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages, shared_items
from periskope_connector.api import inbox
from periskope_connector.patches import type_videos_locations_contacts_polls
from periskope_connector.tests.test_inbox import InboxCase
from periskope_connector.tests.utils import event

VCARD = (
	"BEGIN:VCARD\nVERSION:3.0\nN:;Ishtiaq;;;\nFN:Ishtiaq, Ludiz Brand\n"
	"TEL;waid=918431927493:+91 84319 27493\nEND:VCARD"
)


class TestDetails(FrappeTestCase):
	def test_a_location_with_its_place(self):
		data = {
			"location": {
				"latitude": "11.0168",
				"longitude": 76.9558,
				"name": "Pranera",
				"address": "Tiruppur",
			}
		}
		self.assertEqual(
			shared_items.details("location", data),
			{"name": "Pranera", "address": "Tiruppur", "latitude": 11.0168, "longitude": 76.9558},
		)

	def test_a_location_in_whatsapp_webs_other_shapes(self):
		described = {"location": {"lat": 1.5, "lng": 2.5, "description": "Office\n12 Main Road"}}
		self.assertEqual(
			shared_items.details("location", described),
			{"name": "Office", "address": "12 Main Road", "latitude": 1.5, "longitude": 2.5},
		)
		self.assertEqual(
			shared_items.details("location", {"location": "11.1, 77.3"}),
			{"latitude": 11.1, "longitude": 77.3},
		)
		self.assertIsNone(shared_items.details("location", {"location": None}))

	def test_a_contact_card(self):
		self.assertEqual(
			shared_items.details("contact", {"vcards": [VCARD]}),
			{
				"contacts": [
					{
						"name": "Ishtiaq, Ludiz Brand",
						"phones": [{"number": "+91 84319 27493", "whatsapp": "918431927493"}],
					}
				]
			},
		)

	def test_a_poll_with_its_votes(self):
		data = {
			"poll_info": {"pollName": "Which shade?", "pollOptions": [{"name": "Navy"}, {"name": "Teal"}]},
			"poll_results": {"Navy": ["91999@c.us", "91888@c.us"], "Teal": []},
		}
		self.assertEqual(
			shared_items.details("poll", data),
			{"question": "Which shade?", "options": ["Navy", "Teal"], "votes": {"Navy": 2, "Teal": 0}},
		)

	def test_nothing_for_other_kinds_or_bad_payloads(self):
		self.assertIsNone(shared_items.details("text", {"location": {"lat": 1, "lng": 2}}))
		self.assertIsNone(shared_items.details("contact", "not json"))
		self.assertIsNone(shared_items.details("poll", {"poll_info": {}}))


class TestInInbox(InboxCase):
	def test_a_shared_location_reaches_the_inbox_with_its_place(self):
		self._msg(
			message_type="location",
			body="",
			location={"latitude": 11.0168, "longitude": 76.9558, "name": "Pranera"},
		)
		row = inbox.get_messages(self._chat())["messages"][-1]
		self.assertEqual(row.message_type, "location")
		self.assertEqual(row.shared, {"name": "Pranera", "latitude": 11.0168, "longitude": 76.9558})
		self.assertNotIn("shared_details", row)
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", self._chat(), "last_message_preview"), "[location]"
		)

	def test_messages_saved_as_other_get_their_type_and_details(self):
		name = messages.create_message(
			event(
				message_id=f"old_vcard_{frappe.generate_hash(length=6)}", message_type="vcard", vcards=[VCARD]
			)
		)
		frappe.db.set_value("Periskope Message", name, {"message_type": "other", "shared_details": None})
		type_videos_locations_contacts_polls.execute()
		row = frappe.db.get_value("Periskope Message", name, ["message_type", "shared_details"], as_dict=True)
		self.assertEqual(row.message_type, "contact")
		self.assertEqual(json.loads(row.shared_details)["contacts"][0]["name"], "Ishtiaq, Ludiz Brand")
