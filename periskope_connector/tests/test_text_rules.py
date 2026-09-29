from frappe.tests.utils import FrappeTestCase

from periskope_connector.text_rules import analysis_skip_reason, is_acknowledgement


class TestTextRules(FrappeTestCase):
	def test_tiny_and_one_word_texts_are_not_analysed(self):
		for body in ["done", "Done.", "DONE!!", "ok", "k", "👍", "🙏🙏", "thanks", "received", "confirmed",
			"Interested", "Quotation", "sent", "ok sir", "thank you sir", "Hi", "hello", "good morning", "", "   "]:  # fmt: skip
			self.assertIsNotNone(analysis_skip_reason(body), repr(body))

	def test_real_messages_are_analysed(self):
		for body in ["Price?", "Available?", "Please send the quote", "Send price", "Call me tomorrow",
			"Done, but the delivery was late", "Need 100 pcs", "what is the rate"]:  # fmt: skip
			self.assertIsNone(analysis_skip_reason(body), repr(body))

	def test_reasons(self):
		self.assertEqual(analysis_skip_reason("done"), "Too short to tag")
		self.assertEqual(analysis_skip_reason("Quotation"), "One-word message")
		self.assertEqual(analysis_skip_reason(""), "No text in the message")

	def test_reminders_still_treat_a_real_one_word_message_as_needing_a_reply(self):
		self.assertFalse(is_acknowledgement("Quotation"))
		self.assertTrue(is_acknowledgement("done"))
