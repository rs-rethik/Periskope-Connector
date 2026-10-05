from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import leads, linking, messages
from periskope_connector import timeline_comments as timeline

CHAT = "1203639999@g.us"


def _event(**data):
	base = {
		"message_id": "link_1",
		"chat_id": "919600000001@c.us",
		"from_me": False,
		"org_phone": "919786387999@c.us",
		"sender_phone": "919600000001@c.us",
		"message_type": "chat",
		"body": "Hi, what is the price?",
		"timestamp": "2026-09-29T06:30:00Z",
	}
	base.update(data)
	return {"event_type": "message.created", "data": base}


class TestNameMatching(FrappeTestCase):
	def test_number_key_uses_the_last_ten_digits(self):
		self.assertEqual(linking.number_key("+91 98765-43210"), "9876543210")
		self.assertEqual(linking.number_key("919876543210@c.us"), "9876543210")
		self.assertEqual(linking.number_key("12345"), "")

	def test_names_match_ignores_case_punctuation_and_company_words(self):
		self.assertTrue(linking.names_match("Sumi Export House Pvt. Ltd.", "SUMI EXPORT HOUSE"))
		self.assertTrue(linking.names_match("Sumi Export House - Tirupur", "Sumi Export House"))

	def test_names_do_not_match_when_short_or_different(self):
		self.assertFalse(linking.names_match("Sun", "Sun Tex Mills"))
		self.assertFalse(linking.names_match("Grace Impex", "Coral Reef Cottons"))
		self.assertFalse(linking.names_match("", "Sumi Export House"))


class TestLinking(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target, _value in (
			(patch.object(frappe.db, "commit"), None),
			(
				patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Kongu Region Sales"),
				None,
			),
			(patch.object(messages, "_rep_for_phone", lambda n: None), None),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_link_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_link_test")

	def _lead(self, **values):
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Test", **values})
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		return doc

	def _link(self, name):
		doc = frappe.get_doc("Periskope Message", name)
		return doc.crm_lead, doc.crm_deal, doc.link_status, doc.link_method

	def test_our_own_company_is_never_a_customer(self):
		self._lead(organization="Kongu Region Sales")
		with patch.object(linking, "own_companies", lambda: ["Kongu Region"]):
			name = messages.create_message(_event(message_id="own1", chat_id=CHAT))
		self.assertEqual(frappe.db.get_value("Periskope Group", CHAT, "group_type"), "Internal")
		self.assertEqual(self._link(name)[2], "Internal")

	def test_one_to_one_chat_links_by_mobile_number(self):
		lead = self._lead(mobile_no="+91 96000 00001")
		name = messages.create_message(_event())
		self.assertEqual(self._link(name), (lead.name, None, "Linked", "Mobile Number"))

	def test_two_leads_with_the_same_number_are_ambiguous(self):
		self._lead(mobile_no="9600000001")
		newest = self._lead(mobile_no="9600000001")
		name = messages.create_message(_event())
		lead, _deal, status, _method = self._link(name)
		self.assertEqual((lead, status), (newest.name, "Ambiguous"))

	def test_a_deal_beats_a_lead_with_the_same_number(self):
		other_lead = self._lead(mobile_no="9600000001")
		deal_lead = self._lead()
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": deal_lead.name})
		deal.flags.ignore_mandatory = True
		deal.flags.ignore_links = True
		deal.insert(ignore_permissions=True)
		# the CRM copies mobile_no from the lead on conversion; a raw write skips the doc_events
		# hook that maintains the normalised fields, so run it as well
		frappe.db.set_value("CRM Deal", deal.name, "mobile_no", "9600000001")
		linking.update_phone_fields(frappe.get_doc("CRM Deal", deal.name))
		frappe.db.set_value("CRM Lead", other_lead.name, "modified", "2035-01-01 00:00:00")  # the newer one
		name = messages.create_message(_event())
		self.assertEqual(self._link(name)[:3], (deal_lead.name, deal.name, "Linked"))

	def test_unknown_number_is_no_match(self):
		name = messages.create_message(_event())
		self.assertEqual(self._link(name)[2], "No Match")

	def test_group_named_after_a_customer_is_a_customer_group_linked_by_name(self):
		lead = self._lead(organization="Kongu Region Sales")
		name = messages.create_message(_event(message_id="g1", chat_id=CHAT))
		self.assertEqual(frappe.db.get_value("Periskope Group", CHAT, "group_type"), "Customer")
		self.assertEqual(self._link(name), (lead.name, None, "Linked", "Group Name"))

	def test_customer_group_falls_back_to_the_senders_mobile(self):
		lead = self._lead(organization="Kongu Region Sales")
		other = self._lead(mobile_no="9600000001")
		frappe.db.set_value("CRM Lead", lead.name, "organization", "Someone Else Ltd")
		frappe.db.set_value("Periskope Group", {"chat_id": CHAT}, "group_type", "Customer")
		name = messages.create_message(_event(message_id="g2", chat_id=CHAT))
		frappe.db.set_value("Periskope Group", CHAT, "group_type", "Customer")
		linking.link_message(name)
		self.assertEqual(self._link(name)[0], other.name)

	def test_internal_group_is_not_linked_until_the_ai_names_a_customer(self):
		lead = self._lead(organization="Sumi Export House")
		name = messages.create_message(_event(message_id="g3", chat_id=CHAT, body="make pi"))
		self.assertEqual(frappe.db.get_value("Periskope Group", CHAT, "group_type"), "Internal")
		self.assertEqual(self._link(name)[2:], ("Internal", ""))

		frappe.db.set_value("Periskope Message", name, "mentioned_customer", "SUMI EXPORT HOUSE")
		linking.link_message(name)
		self.assertEqual(self._link(name), (lead.name, None, "Linked", "Customer Name"))

	def test_a_lead_set_on_the_group_wins(self):
		mapped = self._lead(organization="Mapped Customer")
		self._lead(mobile_no="9600000001")
		name = messages.create_message(_event(message_id="g4", chat_id=CHAT))
		frappe.db.set_value("Periskope Group", CHAT, "crm_lead", mapped.name)
		linking.link_message(name)
		self.assertEqual(self._link(name), (mapped.name, None, "Linked", "Group Mapping"))

	def test_a_converted_lead_is_represented_by_its_deal(self):
		lead = self._lead(mobile_no="9600000001")
		frappe.db.set_value("CRM Lead", lead.name, "converted", 1)
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": lead.name, "organization": None})
		deal.flags.ignore_mandatory = True
		deal.flags.ignore_links = True
		deal.insert(ignore_permissions=True)
		name = messages.create_message(_event(message_id="d1"))
		self.assertEqual(self._link(name), (lead.name, deal.name, "Linked", "Mobile Number"))


class TestNewEnquiryLead(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_enquiry_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_enquiry_test")
		# Never write the real setting from a test: the site's own value must stay as it is.
		self.setting = patch.object(leads, "enabled", lambda: True)
		self.setting.start()
		self.addCleanup(self.setting.stop)

	def _tagged(self, message_id="enq1", category="order_or_enquiry", **data):
		name = messages.create_message(_event(message_id=message_id, **data))
		frappe.db.set_value("Periskope Message", name, "ai_category", category)
		return name

	def test_a_new_enquiry_from_an_unknown_number_creates_a_lead(self):
		earlier = messages.create_message(_event(message_id="enq0", body="Hello there, who can help?"))
		name = self._tagged()
		lead_name = leads.create_lead_for_message(name)
		lead = frappe.get_doc("CRM Lead", lead_name)
		self.assertEqual(lead.mobile_no, "+919600000001")
		self.assertEqual((lead.lead_owner, lead.source), ("Administrator", "WhatsApp"))
		for m in (name, earlier):  # the earlier message of the chat is linked too
			self.assertEqual(frappe.db.get_value("Periskope Message", m, "crm_lead"), lead_name)

	def test_a_known_number_gets_no_second_lead(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		before = frappe.db.count("CRM Lead")
		self.assertIsNone(leads.create_lead_for_message(self._tagged()))
		self.assertEqual(frappe.db.count("CRM Lead"), before)

	def test_the_same_number_is_not_turned_into_two_leads(self):
		first = leads.create_lead_for_message(self._tagged("enq_a"))
		second = leads.create_lead_for_message(self._tagged("enq_b"))
		self.assertTrue(first)
		self.assertIsNone(second)

	def test_other_tags_groups_outgoing_and_the_setting_do_not_create_leads(self):
		before = frappe.db.count("CRM Lead")
		leads.create_lead_for_message(self._tagged("o1", category="general"))
		leads.create_lead_for_message(self._tagged("o2", chat_id=CHAT))
		leads.create_lead_for_message(self._tagged("o3", from_me=True))
		with patch.object(leads, "enabled", lambda: False):
			leads.create_lead_for_message(self._tagged("o4"))
		self.assertEqual(frappe.db.count("CRM Lead"), before)

	def test_the_lead_takes_the_name_the_customer_gave(self):
		name = self._tagged("nm1")
		frappe.db.set_value("Periskope Message", name, "contact_name", "Ravi Kumar S")
		lead = frappe.get_doc("CRM Lead", leads.create_lead_for_message(name))
		self.assertEqual((lead.first_name, lead.last_name), ("Ravi", "Kumar S"))

	def test_a_name_given_earlier_in_the_chat_is_used(self):
		earlier = messages.create_message(_event(message_id="nm0", body="Hi, I am Asha"))
		frappe.db.set_value("Periskope Message", earlier, "contact_name", "Asha")
		lead = frappe.get_doc("CRM Lead", leads.create_lead_for_message(self._tagged("nm2")))
		self.assertEqual(lead.first_name, "Asha")

	def test_without_a_name_the_phone_number_is_the_first_name(self):
		lead = frappe.get_doc("CRM Lead", leads.create_lead_for_message(self._tagged("nm3")))
		self.assertEqual((lead.first_name, lead.last_name), ("919600000001", ""))

	def test_the_setting_is_off_until_it_is_saved_on(self):
		self.setting.stop()  # the real leads.enabled, reading a faked stored value
		self.addCleanup(self.setting.start)
		for stored, expected in (((), False), ((("0",),), False), ((("1",),), True)):
			with patch.object(frappe.db, "sql", lambda *a, _s=stored, **k: _s):
				self.assertEqual(leads.enabled(), expected, stored)


class TestTimelineComment(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_comment_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_comment_test")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		self.lead = lead.insert(ignore_permissions=True)

	def _message(self, message_id, category=None, body="Need <b>10</b> pieces\nurgent"):
		name = messages.create_message(_event(message_id=message_id, body=body))
		if category:
			frappe.db.set_value(
				"Periskope Message", name, {"ai_category": category, "ai_summary": "Wants ten pieces"}
			)
		return name

	def _comments(self):
		return frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "CRM Lead",
				"reference_name": self.lead.name,
				"comment_type": "Comment",
			},
			fields=["name", "content", "owner"],
		)

	def test_a_tagged_linked_message_gets_one_escaped_comment_on_the_lead(self):
		name = self._message("c1", "order_or_enquiry")
		linking.link_message(name)
		linking.link_message(name)  # linking again must not add a second comment
		comments = self._comments()
		self.assertEqual(len(comments), 1)
		content = comments[0].content
		self.assertIn("WhatsApp incoming", content)
		self.assertIn("&lt;b&gt;10&lt;/b&gt; pieces<br>urgent", content)  # no HTML from the customer
		self.assertIn("order or enquiry: Wants ten pieces", content)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "timeline_comment"), comments[0].name)

	def test_unclassified_and_general_messages_get_no_comment(self):
		linking.link_message(self._message("c2"))
		linking.link_message(self._message("c2b", "general"))
		self.assertEqual(self._comments(), [])

	def test_a_message_classified_after_it_was_linked_gets_its_comment_when_linked_again(self):
		name = self._message("c3")
		self.assertEqual(self._comments(), [])
		frappe.db.set_value("Periskope Message", name, "ai_category", "delivery_delay")
		linking.link_message(name)
		self.assertEqual(len(self._comments()), 1)

	def test_an_unlinked_message_gets_no_comment(self):
		name = messages.create_message(_event(message_id="c4", chat_id="919888800000@c.us"))
		frappe.db.set_value("Periskope Message", name, "ai_category", "delivery_delay")
		linking.link_message(name)
		self.assertFalse(frappe.db.get_value("Periskope Message", name, "timeline_comment"))


class TestBurstComments(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_burst_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_burst_test")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		self.lead = lead.insert(ignore_permissions=True)

	def _commented(self, message_id):
		name = frappe.db.get_value("Periskope Message", {"message_id": message_id})
		return timeline.add_comment(name)

	def _tagged_message(self, message_id, minutes_later=0):
		name = messages.create_message(_event(message_id=message_id, body=f"message {message_id}"))
		if minutes_later:
			base = frappe.db.get_value("Periskope Message", {"message_id": message_id}, "message_timestamp")
			frappe.db.set_value(
				"Periskope Message", name, "message_timestamp", add_minutes(base, minutes_later)
			)
		frappe.db.set_value("Periskope Message", name, "ai_category", "order_or_enquiry")
		return name

	def test_burst_mode_merges_messages_inside_the_window(self):
		with patch.object(timeline, "_settings", lambda: (timeline.MODE_BURST, 30)):
			self._tagged_message("b1")
			second = self._tagged_message("b2", minutes_later=10)
			first_comment = self._commented("b1")
			self._commented("b2")
			comments = frappe.get_all(
				"Comment",
				filters={"reference_doctype": "CRM Lead", "reference_name": self.lead.name},
				pluck="name",
			)
			self.assertEqual(len(comments), 1)  # both messages share one comment
			self.assertEqual(
				frappe.db.get_value("Periskope Message", second, "timeline_comment"), first_comment
			)
			content = frappe.db.get_value("Comment", first_comment, "content")
			self.assertIn("message b1", content)
			self.assertIn("message b2", content)

	def test_burst_mode_starts_a_new_comment_outside_the_window(self):
		with patch.object(timeline, "_settings", lambda: (timeline.MODE_BURST, 30)):
			self._tagged_message("b3")
			self._tagged_message("b4", minutes_later=120)
			self._commented("b3")
			self._commented("b4")
			self.assertEqual(
				frappe.db.count(
					"Comment", {"reference_doctype": "CRM Lead", "reference_name": self.lead.name}
				),
				2,
			)

	def test_off_mode_adds_nothing(self):
		with patch.object(timeline, "_settings", lambda: (timeline.MODE_OFF, 30)):
			self._tagged_message("b5")
			self.assertIsNone(self._commented("b5"))
			self.assertEqual(
				frappe.db.count(
					"Comment", {"reference_doctype": "CRM Lead", "reference_name": self.lead.name}
				),
				0,
			)


def add_minutes(value, minutes):
	from datetime import timedelta

	return value + timedelta(minutes=minutes)
