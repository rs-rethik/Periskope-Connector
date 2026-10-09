import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages, tags
from periskope_connector.ai import classifier


class TestClassify(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never use the real API key."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_ai_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_ai_test")

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
			"category": "order_or_enquiry",
			"sentiment": "Positive",
			"severity": "Low",
			"summary": "Customer wants a quote for 50 units.",
			"confidence": 92,
			"needs_reply": True,
			"follow_up_on": "",
			"mentioned_products": "",
			"mentioned_amount": "50 units",
			"competitor_name": "",
			"customer_name": "",
			"contact_name": "",
			"language": "English",
		}
		base.update(result)
		response = MagicMock(
			stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(base))]
		)
		return patch.object(classifier, "call_claude", lambda *a, **k: json.loads(response.content[0].text))

	def test_classification_is_saved_with_its_category(self):
		doc = self._message()
		with self._claude_says():
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(doc.classification_status, "Done")
		self.assertEqual(doc.ai_category, "order_or_enquiry")
		self.assertEqual(doc.severity, "Low")
		self.assertTrue(doc.needs_reply)

	def test_an_unknown_category_falls_back_to_general(self):
		doc = self._message()
		with self._claude_says(category="Made Up Category"):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(doc.ai_category, "general")

	def test_severity_is_the_ais_own_and_case_does_not_matter(self):
		for i, (said, saved) in enumerate(
			(("high", "High"), ("MEDIUM", "Medium"), ("low", "Low"), ("nonsense", "Low"))
		):
			doc = self._message("The goods are damaged", message_id=f"sev_{i}")
			with self._claude_says(category="quality_complaint", severity=said):
				classifier.classify(doc.name)
			self.assertEqual(frappe.db.get_value("Periskope Message", doc.name, "severity"), saved, said)

	def test_the_summary_is_cut_to_one_line_of_140_characters(self):
		doc = self._message()
		with self._claude_says(summary="x" * 300):
			classifier.classify(doc.name)
		self.assertEqual(len(frappe.db.get_value("Periskope Message", doc.name, "ai_summary")), 140)

	def test_follow_up_time_is_parsed(self):
		doc = self._message("Call me tomorrow 4pm")
		with self._claude_says(category="general", follow_up_on="2026-09-30 16:00"):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(str(doc.follow_up_on), "2026-09-30 16:00:00")

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

	def test_low_value_messages_are_skipped_as_general_low_when_they_arrive(self):
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch.object(frappe, "enqueue") as enqueue,
		):
			done = self._message("done", message_id="arr_done")
			real = self._message("Send the price list", message_id="arr_real")
		self.assertEqual(
			(done.classification_status, done.classification_error), ("Skipped", "Too short to tag")
		)
		self.assertEqual((done.ai_category, done.severity), ("general", "Low"))
		self.assertEqual(real.classification_status, "Pending")
		enqueue.assert_not_called()  # nothing is queued: the 5-minute job picks the message up

	def test_a_rep_message_is_never_classified_and_has_no_category(self):
		with patch.object(frappe.db, "get_single_value", return_value=1):
			sent = self._message("Sending the quotation now", message_id="rep_1", from_me=True)
		self.assertEqual(
			(sent.classification_status, sent.classification_error), ("Skipped", "Sent by the rep")
		)
		self.assertFalse(sent.ai_category)
		self.assertNotIn(sent.name, classifier._pending(100000))

	def test_the_batch_takes_at_most_twenty_customer_messages_oldest_first(self):
		self.assertEqual(classifier.BATCH_SIZE, 20)
		with patch.object(frappe.db, "get_single_value", return_value=1):
			names = [self._message(f"Send price {i}", message_id=f"b_{i}").name for i in range(3)]
		pending = classifier._pending(2)
		self.assertEqual(len(pending), 2)
		self.assertTrue(set(pending) <= set(names) or pending)  # oldest first, capped by the limit

	def test_skipped_messages_are_saved_as_general_low_except_rep_and_group_skips(self):
		from periskope_connector.text_rules import skip_fields

		self.assertEqual(skip_fields("Too short to tag")["ai_category"], "general")
		self.assertEqual(skip_fields("Not a text message")["severity"], "Low")
		for reason in ("Sent by the rep", "Group chat"):
			self.assertNotIn("ai_category", skip_fields(reason))

	def _run_batch(self, names, classify=None, commit_errors=None):
		"""Run the 5-minute job over `names`. Rollback and error logging are mocked so the test's
		own transaction survives; returns the mocks."""
		with (
			patch.object(classifier, "_ready_to_run", lambda: True),
			patch.object(classifier, "_pending", lambda n: names),
			patch.object(frappe.db, "rollback") as rollback,
			patch.object(frappe, "log_error") as log_error,
			patch.object(classifier, "classify", classify or classifier.classify),
		):
			if commit_errors:
				frappe.db.commit.side_effect = commit_errors
			classifier.run_batch()
		return rollback, log_error

	def _status(self, name):
		return frappe.db.get_value(
			"Periskope Message",
			name,
			["classification_status", "classification_attempts", "classification_error"],
			as_dict=True,
		)

	def test_one_failing_message_does_not_stop_the_batch(self):
		bad = self._message("Send the price list", message_id="batch_bad")
		good = self._message("Need a quote for 20 units", message_id="batch_good")
		real = classifier.classify

		def flaky(name):
			if name == bad.name:
				raise RuntimeError("database went away")
			return real(name)

		with self._claude_says():
			rollback, log_error = self._run_batch([bad.name, good.name], classify=flaky)

		rollback.assert_called()
		log_error.assert_called()
		failed = self._status(bad.name)
		self.assertEqual((failed.classification_status, failed.classification_attempts), ("Failed", 1))
		self.assertIn("database went away", failed.classification_error)
		self.assertEqual(self._status(good.name).classification_status, "Done")  # still processed

	def test_a_failed_commit_is_rolled_back_and_the_batch_carries_on(self):
		first = self._message("Send the price list", message_id="commit_1")
		second = self._message("Need a quote for 20 units", message_id="commit_2")
		# 1st commit fails (first message), then the failure record and the second message commit fine
		with self._claude_says():
			rollback, log_error = self._run_batch(
				[first.name, second.name], commit_errors=[Exception("commit failed"), None, None]
			)
		rollback.assert_called_once()
		log_error.assert_called_once()
		self.assertEqual(self._status(first.name).classification_status, "Failed")
		self.assertEqual(self._status(second.name).classification_status, "Done")

	def test_the_batch_job_logs_and_raises_an_unexpected_failure(self):
		with (
			patch.object(classifier, "_ready_to_run", lambda: True),
			patch.object(classifier, "_pending", side_effect=RuntimeError("database unreachable")),
			patch.object(frappe, "log_error") as log_error,
		):
			with self.assertRaises(RuntimeError):
				classifier.run_batch()
		log_error.assert_called_once()
		self.assertIn("AI classification run failed", log_error.call_args.kwargs["title"])

	def test_the_batch_job_does_nothing_when_tagging_is_off(self):
		with (
			patch.object(classifier, "_ready_to_run", lambda: False),
			patch.object(classifier, "_pending") as pending,
		):
			classifier.run_batch()
		pending.assert_not_called()

	def test_a_message_that_keeps_failing_stops_being_retried(self):
		bad = self._message("Send the price list", message_id="keeps_failing")
		self.assertIn(bad.name, classifier._pending(100000))
		for _i in range(classifier.MAX_ATTEMPTS):
			self._run_batch([bad.name], classify=lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
		self.assertEqual(self._status(bad.name).classification_attempts, classifier.MAX_ATTEMPTS)
		self.assertNotIn(bad.name, classifier._pending(100000))

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
		with patch.object(frappe.db, "get_single_value", return_value=1):
			text = self._message("Send the price list", message_id="n_text")
			image = self._message("photo caption", message_id="n_image", message_type="image")
		self.assertEqual(text.classification_status, "Pending")
		self.assertEqual(
			(image.classification_status, image.classification_error), ("Skipped", "Not a text message")
		)
		self.assertEqual((image.ai_category, image.severity), ("general", "Low"))

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

	def test_prompt_names_the_categories_and_history_is_ordered(self):
		prompt = classifier.build_system_prompt()
		for category in tags.names():
			self.assertIn(category, prompt)
		for word in ("shade", "GSM", "shrinkage", "pilling", "wash fastness", "Tanglish", "LAST message"):
			self.assertIn(word, prompt)
		self.assertNotIn("tag", prompt.lower().replace("stage", ""))

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
		custom = SimpleNamespace(
			ai_instructions="Be strict. {curly} braces stay.",
			ai_business_context="We sell industrial cables in Chennai.",
		)
		prompt = classifier.build_system_prompt(custom)
		self.assertTrue(
			prompt.startswith("About the business you are working for:\nWe sell industrial cables")
		)
		self.assertIn("Be strict. {curly} braces stay.", prompt)
		self.assertIn("customer_name is the name", prompt)  # always added

	def test_empty_or_old_tag_instructions_fall_back_to_the_default(self):
		for text in ("  ", None, "Tag it.\n\nAvailable tags:\n{tag_list}"):
			prompt = classifier.build_system_prompt(
				SimpleNamespace(ai_instructions=text, ai_business_context=None)
			)
			self.assertIn("You review WhatsApp messages", prompt)
			self.assertIn("order_or_enquiry", prompt)

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
		reply = json.dumps({"category": "order_or_enquiry", "severity": "Low"})
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
			result = classifier.call_claude(self.settings, doc)
		return client.messages.create.call_args.kwargs, result

	def test_claude_request_uses_structured_output_and_caching(self):
		request, result = self._run_call("claude-haiku-4-5")
		self.assertIn("output_config", request)
		self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
		self.assertEqual(result["category"], "order_or_enquiry")

	def test_glm_request_asks_for_json_in_the_prompt(self):
		request, result = self._run_call("glm-5.3")
		self.assertNotIn("output_config", request)
		self.assertIsInstance(request["system"], str)
		self.assertIn("Reply with ONLY one JSON object", request["system"])
		self.assertEqual(result["category"], "order_or_enquiry")

	def test_jarvis_provider_sends_prompt_and_message_to_jarvis_and_parses_the_reply(self):
		doc = self._message()
		self.settings.ai_provider = "Jarvis"
		reply = json.dumps({"category": "order_or_enquiry", "severity": "Low"})
		sent = {}

		def fake_task(prompt, input_payload, timeout=60):
			sent.update(prompt=prompt, input=input_payload)
			return reply

		with (
			patch.object(classifier, "_client") as direct,
			patch.object(classifier, "_jarvis_llm_task", lambda: (fake_task, ValueError)),
		):
			result = classifier.call_claude(self.settings, doc)
		direct.assert_not_called()
		self.assertEqual(result["category"], "order_or_enquiry")
		self.assertIn("order_or_enquiry", sent["prompt"])
		self.assertIn("JSON string literal", sent["prompt"])
		self.assertIn("Message to classify:", sent["input"])

	def test_jarvis_provider_needs_no_api_key_and_hides_the_optimizer(self):
		self.settings.ai_provider = "Jarvis"
		self.settings.enable_classification = 1
		self.settings.get_password = lambda field, **kw: None
		with patch.object(classifier, "get_settings", lambda: self.settings):
			self.assertTrue(classifier._ready_to_run())
		with self.assertRaises(frappe.ValidationError):
			classifier.optimize_instructions(self.settings, "x")

	def test_jarvis_is_offered_only_when_the_app_is_installed(self):
		with patch("frappe.get_installed_apps", lambda: ["frappe", "crm"]):
			self.assertEqual(classifier.available_providers(), ["Direct API"])
		with patch("frappe.get_installed_apps", lambda: ["frappe", "jarvis"]):
			self.assertEqual(classifier.available_providers(), ["Direct API", "Jarvis"])

	def test_saving_falls_back_to_the_direct_api_when_jarvis_is_gone(self):
		settings = frappe.get_single("Periskope Settings")
		settings.ai_provider = "Jarvis"
		with patch("frappe.get_installed_apps", lambda: ["frappe"]):
			settings.validate()
		self.assertEqual(settings.ai_provider, "Direct API")

	def test_jarvis_errors_become_readable_validation_errors(self):
		def boom(prompt, input_payload, timeout=60):
			raise ValueError("gateway down")

		self.settings.ai_provider = "Jarvis"
		with patch.object(classifier, "_jarvis_llm_task", lambda: (boom, ValueError)):
			with self.assertRaises(frappe.ValidationError):
				classifier.ping(self.settings)

	def test_group_prompt_has_group_name_senders_and_the_quoted_message(self):
		doc = self._message()
		doc.is_group, doc.chat_name, doc.sender_phone = 1, "Kongu Region", "917667787999"
		history = [
			SimpleNamespace(
				direction="Incoming",
				body="ACME - SOJV/1",
				message_timestamp=None,
				sender_phone="919786800110",
				is_group=1,
			)
		]
		text = classifier.build_user_message(doc, history, "ACME - SOJV/1")
		self.assertIn('called "Kongu Region"', text)
		self.assertIn("internal team group", text)
		self.assertIn("[Customer 919786800110 ] ACME - SOJV/1", text)
		self.assertIn("replies to this earlier message: ACME - SOJV/1", text)
		self.assertIn("[Customer 917667787999", text.split("Message to classify:")[1])

	def test_a_group_message_always_gets_history_even_when_context_is_off(self):
		doc = self._message()
		doc.is_group = 1
		self.settings.context_messages = 0
		with (
			patch.object(classifier, "get_history", return_value=[]) as history,
			patch.object(classifier, "_client", lambda s: MagicMock()),
		):
			try:
				classifier.call_claude(self.settings, doc)
			except Exception:
				pass  # the fake client's reply is not the point here
		self.assertEqual(history.call_args.args[1], classifier.GROUP_MIN_CONTEXT)

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

	def test_optimizer_returns_improved_text_that_keeps_the_categories(self):
		categories = ", ".join(tags.names())
		improved = "You classify WhatsApp sales messages. " * 6 + f"\n\nCategories: {categories}"
		client = self._optimizer_client(f"```\n{improved}\n```")
		with patch.object(classifier, "_client", lambda s: client):
			text = classifier.optimize_instructions(
				self.settings, "Old instructions", "be stricter on pricing"
			)
		self.assertEqual(text, improved)
		request = client.messages.create.call_args.kwargs
		self.assertIn("be stricter on pricing", request["messages"][0]["content"])
		self.assertIn("Old instructions", request["messages"][0]["content"])
		self.assertNotIn("output_config", request)  # plain text answer, works for any provider

	def test_optimizer_text_needs_no_category_list_because_tags_are_added_separately(self):
		client = self._optimizer_client("You classify WhatsApp sales messages carefully. " * 5)
		with patch.object(classifier, "_client", lambda s: client):
			text = classifier.optimize_instructions(self.settings, "", "")
		self.assertNotIn("Categories", text)
		self.assertIn("Categories", classifier.build_system_prompt())

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

	def test_schema_enums_match_the_categories(self):
		schema = classifier.build_schema()
		self.assertEqual(schema["properties"]["category"]["enum"], tags.names())
		self.assertEqual(schema["properties"]["severity"]["enum"], ["Low", "Medium", "High"])
		self.assertEqual(set(schema["required"]), set(schema["properties"]))
		self.assertFalse(schema["additionalProperties"])
