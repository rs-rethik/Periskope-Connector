import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import default_tags, messages
from periskope_connector.ai import classifier


def _tag(name, action="None", **kw):
	return {"tag_name": name, "name": name, "fcrm_action": action, **kw}


class TestResolveActions(FrappeTestCase):
	def test_no_tags_means_no_action(self):
		self.assertEqual(classifier.resolve_actions([]), ([], "None"))

	def test_headline_priority(self):
		tags = [
			_tag("Callback Requested", "Create Task", task_title="Call back", task_priority="High"),
			_tag("Complaint", "Escalate", escalation_severity="High"),
			_tag("Pricing Request", "Create Lead"),
		]
		actions, headline = classifier.resolve_actions(tags)
		self.assertEqual(headline, "Escalate")
		self.assertEqual({a["action"] for a in actions}, {"Create Task", "Escalate", "Create Lead"})

	def test_one_tag_can_carry_several_actions(self):
		tag = _tag(
			"Demo / Trial Request",
			"Create Task",
			target_deal_status="Demo/Making",
			task_title="Schedule demo",
			task_priority="Medium",
		)
		actions, headline = classifier.resolve_actions([tag], follow_up_on="2026-10-01 16:00:00")
		self.assertEqual(headline, "Update Deal Status")
		task = next(a for a in actions if a["action"] == "Create Task")
		self.assertEqual((task["title"], task["due"]), ("Schedule demo", "2026-10-01 16:00:00"))

	def test_lost_reason_marks_lost(self):
		actions, headline = classifier.resolve_actions(
			[_tag("Lost to Competitor", "Mark Lost", lost_reason="Competition")]
		)
		self.assertEqual(headline, "Mark Lost")
		self.assertEqual(actions[0]["lost_reason"], "Competition")


class TestDefaultTags(FrappeTestCase):
	def test_tag_definitions_are_consistent(self):
		names = [t[1] for t in default_tags.TAGS]
		self.assertEqual(len(names), len(set(names)), "duplicate tag names")
		self.assertGreaterEqual(len(names), 60)
		categories = {t[0] for t in default_tags.TAGS}
		self.assertEqual(len(categories), 7)
		for _category, name, action, extra, description in default_tags.TAGS:
			self.assertTrue(description, name)
			if action == "Escalate":
				self.assertIn("escalation_severity", extra, name)
			if action == "Mark Lost":
				self.assertIn("lost_reason", extra, name)


class TestClassify(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never use the real API key."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_ai_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_ai_test")

		default_tags.create_default_tags()
		rep = patch.object(messages, "_rep_for_phone", lambda n: None)
		rep.start()
		self.addCleanup(rep.stop)

		self.settings = SimpleNamespace(
			classify_group_chats=0, context_messages=5, ai_model="claude-haiku-4-5"
		)
		patch.object(classifier, "get_settings", lambda: self.settings).start()
		self.addCleanup(patch.stopall)

	def _message(self, body="Please send a quote for 50 units", **kw):
		payload = {
			"event_type": "message.created",
			"data": {
				"message_id": kw.pop("message_id", "false_1@c.us_X1"),
				"chat_id": "919600000001@c.us",
				"from_me": False,
				"org_phone": "919786387999@c.us",
				"message_type": "chat",
				"body": body,
				"timestamp": "2026-09-29T06:30:00Z",
				**kw,
			},
		}
		return frappe.get_doc("Periskope Message", messages.create_message(payload))

	def _claude_says(self, **result):
		base = {
			"tags": ["Quotation Request", "Bulk / Large Order Enquiry"],
			"primary_tag": "Quotation Request",
			"sentiment": "Positive",
			"severity": "None",
			"summary": "Customer wants a quote for 50 units.",
			"confidence": 92,
			"needs_reply": True,
			"follow_up_on": "",
			"mentioned_products": "",
			"mentioned_amount": "50 units",
			"competitor_name": "",
			"language": "English",
		}
		base.update(result)
		response = MagicMock(
			stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(base))]
		)
		return patch.object(classifier, "call_claude", lambda *a, **k: json.loads(response.content[0].text))

	def test_classification_saved_with_suggested_actions(self):
		doc = self._message()
		with self._claude_says():
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(doc.classification_status, "Done")
		self.assertEqual(doc.primary_tag, "Quotation Request")
		self.assertEqual({t.tag for t in doc.tags}, {"Quotation Request", "Bulk / Large Order Enquiry"})
		self.assertEqual(doc.suggested_action, "Create Lead")
		self.assertTrue(doc.needs_reply)
		actions = json.loads(doc.suggested_actions)
		self.assertIn("Create Task", {a["action"] for a in actions})

	def test_unknown_tags_are_dropped_and_primary_falls_back(self):
		doc = self._message()
		with self._claude_says(tags=["Made Up Tag", "Complaint"], primary_tag="Made Up Tag"):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual([t.tag for t in doc.tags], ["Complaint"])
		self.assertEqual(doc.primary_tag, "Complaint")

	def test_escalation_tag_raises_severity(self):
		doc = self._message("This is unacceptable, I will cancel the order")
		with self._claude_says(
			tags=["Cancellation Threat / Churn Risk"],
			primary_tag="Cancellation Threat / Churn Risk",
			severity="Low",
			sentiment="Negative",
		):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(doc.severity, "High")
		self.assertEqual(doc.suggested_action, "Escalate")

	def test_follow_up_time_is_parsed(self):
		doc = self._message("Call me tomorrow 4pm")
		with self._claude_says(
			tags=["Callback Requested"], primary_tag="Callback Requested", follow_up_on="2026-09-30 16:00"
		):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(str(doc.follow_up_on), "2026-09-30 16:00:00")
		task = next(a for a in json.loads(doc.suggested_actions) if a["action"] == "Create Task")
		self.assertEqual(task["due"], "2026-09-30 16:00:00")

	def test_trivial_and_group_messages_are_skipped(self):
		doc = self._message("ok", message_id="t1")
		classifier.classify(doc.name)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", doc.name, "classification_status"), "Skipped"
		)

		group = self._message("Any update on the order?", message_id="g1", chat_id="1203630@g.us")
		classifier.classify(group.name)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", group.name, "classification_status"), "Skipped"
		)

	def test_one_word_messages_are_never_sent_to_the_ai(self):
		for i, body in enumerate(["done", "Received", "Quotation", "👍"]):
			doc = self._message(body, message_id=f"one_{i}")
			with patch.object(classifier, "call_claude") as ai:
				classifier.classify(doc.name)
			ai.assert_not_called()
			self.assertEqual(
				frappe.db.get_value("Periskope Message", doc.name, "classification_status"), "Skipped", body
			)

	def test_low_value_messages_are_marked_skipped_when_they_arrive(self):
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch.object(frappe, "enqueue") as enqueue,
		):
			done = self._message("done", message_id="arr_done")
			real = self._message("Send the price list", message_id="arr_real")
		self.assertEqual(
			(done.classification_status, done.classification_error), ("Skipped", "Too short to tag")
		)
		self.assertEqual(real.classification_status, "Pending")
		enqueue.assert_called_once()  # only the real message is queued

	def test_only_text_chats_are_analysed(self):
		for kind in ("image", "document", "ptt", "video", "vcard", "sticker"):
			doc = self._message(
				"Please see the attached quotation", message_id=f"m_{kind}", message_type=kind
			)
			with patch.object(classifier, "call_claude") as ai:
				classifier.classify(doc.name)
			ai.assert_not_called()
			row = frappe.db.get_value(
				"Periskope Message", doc.name, ["classification_status", "classification_error"], as_dict=True
			)
			self.assertEqual(
				(row.classification_status, row.classification_error), ("Skipped", "Not a text message"), kind
			)

	def test_new_messages_are_marked_by_type_when_tagging_is_on(self):
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch.object(frappe, "enqueue") as enqueue,
		):
			text = self._message("Send the price list", message_id="n_text")
			image = self._message("photo caption", message_id="n_image", message_type="image")
		self.assertEqual(text.classification_status, "Pending")
		self.assertEqual(
			(image.classification_status, image.classification_error), ("Skipped", "Not a text message")
		)
		enqueue.assert_called_once()  # only the text message is queued for the AI

	def test_history_context_contains_only_text_chats(self):
		self._message("Do you have 10mm cable?", message_id="c1", timestamp="2026-09-29T06:00:00Z")
		self._message(
			"scan of the datasheet",
			message_id="c2",
			message_type="document",
			timestamp="2026-09-29T06:05:00Z",
		)
		current = self._message("Send price", message_id="c3", timestamp="2026-09-29T06:20:00Z")
		history = classifier.get_history(current, 5)
		self.assertEqual([h.body for h in history], ["Do you have 10mm cable?"])

	def test_failure_is_recorded_and_counted(self):
		doc = self._message()
		with (
			patch.object(classifier, "call_claude", side_effect=ValueError("boom")),
			patch.object(frappe, "log_error"),
		):
			classifier.classify(doc.name)
		row = frappe.db.get_value(
			"Periskope Message",
			doc.name,
			["classification_status", "classification_attempts", "classification_error"],
			as_dict=True,
		)
		self.assertEqual((row.classification_status, row.classification_attempts), ("Failed", 1))
		self.assertIn("boom", row.classification_error)

	def test_prompt_lists_tags_and_history_is_ordered(self):
		prompt = classifier.build_system_prompt(classifier.enabled_tags())
		self.assertIn("Quotation Request:", prompt)
		self.assertIn("Legal Threat:", prompt)
		self.assertIn("Issue & Escalation", prompt)

		first = self._message("Do you have 10mm cable?", message_id="h1", timestamp="2026-09-29T06:00:00Z")
		second = self._message(
			"Yes, in stock", message_id="h2", from_me=True, timestamp="2026-09-29T06:10:00Z"
		)
		current = self._message("Send price", message_id="h3", timestamp="2026-09-29T06:20:00Z")
		history = classifier.get_history(current, 5)
		self.assertEqual([h.body for h in history], [first.body, second.body])
		text = classifier.build_user_message(current, history)
		self.assertIn("[Rep ", text)
		self.assertIn("Message to classify:", text)
		self.assertNotIn("919600000001", text)

	def test_prompt_uses_instructions_and_business_note_from_settings(self):
		tags = classifier.enabled_tags()
		custom = SimpleNamespace(
			ai_instructions="Be strict. {curly} braces stay.\n\nTags:\n{tag_list}",
			ai_business_context="We sell industrial cables in Chennai.",
		)
		prompt = classifier.build_system_prompt(tags, custom)
		self.assertTrue(
			prompt.startswith("About the business you are working for:\nWe sell industrial cables")
		)
		self.assertIn("Be strict. {curly} braces stay.", prompt)
		self.assertIn("Quotation Request:", prompt)
		self.assertNotIn("{tag_list}", prompt)

	def test_prompt_appends_tags_when_placeholder_is_missing(self):
		custom = SimpleNamespace(ai_instructions="Classify carefully.", ai_business_context="")
		prompt = classifier.build_system_prompt(classifier.enabled_tags(), custom)
		self.assertTrue(prompt.startswith("Classify carefully."))
		self.assertIn("Available tags, by category:", prompt)
		self.assertIn("Legal Threat:", prompt)

	def test_empty_instructions_fall_back_to_default(self):
		empty = SimpleNamespace(ai_instructions="  ", ai_business_context=None)
		prompt = classifier.build_system_prompt(classifier.enabled_tags(), empty)
		self.assertIn("You classify WhatsApp messages", prompt)

	def test_parse_json_reply_tolerates_fences_and_extra_words(self):
		self.assertEqual(classifier.parse_json_reply('```json\n{"a": 1}\n```'), {"a": 1})
		self.assertEqual(
			classifier.parse_json_reply('Sure! Here it is: {"a": {"b": 2}} Hope that helps.'), {"a": {"b": 2}}
		)
		self.assertRaises(ValueError, classifier.parse_json_reply, "no json here")

	def _run_call(self, model):
		"""Run call_claude against a fake client and return the request it sent."""
		doc = self._message()
		self.settings.ai_model = model
		reply = json.dumps({"tags": ["Quotation Request"], "primary_tag": "Quotation Request"})
		client = MagicMock()
		client.messages.create.return_value = SimpleNamespace(
			stop_reason="end_turn",
			content=[
				SimpleNamespace(type="thinking", text=None),
				SimpleNamespace(
					type="text", text=f"```json\n{reply}\n```" if not classifier.is_claude(model) else reply
				),
			],
		)
		with patch.object(classifier, "_client", lambda s: client):
			result = classifier.call_claude(self.settings, doc, classifier.enabled_tags())
		return client.messages.create.call_args.kwargs, result

	def test_claude_request_uses_structured_output_and_caching(self):
		request, result = self._run_call("claude-haiku-4-5")
		self.assertIn("output_config", request)
		self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
		self.assertEqual(result["primary_tag"], "Quotation Request")

	def test_glm_request_asks_for_json_in_the_prompt(self):
		request, result = self._run_call("glm-5.3")
		self.assertNotIn("output_config", request)
		self.assertIsInstance(request["system"], str)
		self.assertIn("Reply with ONLY one JSON object", request["system"])
		self.assertEqual(result["primary_tag"], "Quotation Request")

	def test_custom_base_url_and_bearer_header(self):
		self.settings.ai_base_url = " https://api.z.ai/api/anthropic/ "
		self.settings.get_password = lambda field, **kw: "secret-key"
		with patch("anthropic.Anthropic") as anthropic_cls:
			classifier._client(self.settings)
		kwargs = anthropic_cls.call_args.kwargs
		self.assertEqual(kwargs["base_url"], "https://api.z.ai/api/anthropic")
		self.assertEqual(kwargs["default_headers"], {"Authorization": "Bearer secret-key"})

	def test_glm_model_defaults_to_zai_address_and_claude_to_anthropic(self):
		self.settings.get_password = lambda field, **kw: "k"
		self.settings.ai_base_url = None
		self.settings.ai_model = "glm-5.3"
		with patch("anthropic.Anthropic") as anthropic_cls:
			classifier._client(self.settings)
		self.assertEqual(anthropic_cls.call_args.kwargs["base_url"], "https://api.z.ai/api/anthropic")

		self.settings.ai_model = "claude-haiku-4-5"
		with patch("anthropic.Anthropic") as anthropic_cls:
			classifier._client(self.settings)
		self.assertNotIn("base_url", anthropic_cls.call_args.kwargs)

	def _optimizer_client(self, text, stop_reason="end_turn"):
		client = MagicMock()
		client.messages.create.return_value = SimpleNamespace(
			stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
		)
		return client

	def test_optimizer_returns_improved_text_and_keeps_tag_placeholder(self):
		improved = "You tag WhatsApp sales messages. " * 6 + "\n\nTags:\n{tag_list}"
		client = self._optimizer_client(f"```\n{improved}\n```")
		with patch.object(classifier, "_client", lambda s: client):
			text = classifier.optimize_instructions(
				self.settings, "Old instructions {tag_list}", "be stricter on pricing"
			)
		self.assertEqual(text, improved)
		request = client.messages.create.call_args.kwargs
		self.assertIn("be stricter on pricing", request["messages"][0]["content"])
		self.assertIn("Old instructions", request["messages"][0]["content"])
		self.assertNotIn("output_config", request)  # plain text answer, works for any provider

	def test_optimizer_re_adds_missing_tag_placeholder(self):
		client = self._optimizer_client("You tag WhatsApp sales messages carefully. " * 5)
		with patch.object(classifier, "_client", lambda s: client):
			text = classifier.optimize_instructions(self.settings, "", "")
		self.assertEqual(text.count("{tag_list}"), 1)
		self.assertIn("Available tags, by category:", text)

	def test_optimizer_rejects_empty_or_cut_off_answers(self):
		for client in (self._optimizer_client("Sorry"), self._optimizer_client("x" * 500, "max_tokens")):
			with patch.object(classifier, "_client", lambda s, c=client: c):
				self.assertRaises(
					frappe.ValidationError, classifier.optimize_instructions, self.settings, "abc", ""
				)

	def test_optimizer_turns_provider_errors_into_a_clear_message(self):
		client = MagicMock()
		client.messages.create.side_effect = ConnectionError("down")
		with patch.object(classifier, "_client", lambda s: client):
			self.assertRaises(
				frappe.ValidationError, classifier.optimize_instructions, self.settings, "abc", ""
			)

	def test_schema_enum_matches_tags(self):
		schema = classifier.build_schema(["A", "B"])
		self.assertEqual(schema["properties"]["primary_tag"]["enum"], ["A", "B"])
		self.assertEqual(set(schema["required"]), set(schema["properties"]))
		self.assertFalse(schema["additionalProperties"])
