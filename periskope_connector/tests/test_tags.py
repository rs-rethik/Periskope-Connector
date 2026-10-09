"""Tags: the AI's categories are records the team edits, and the classifier follows them."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import install, tags
from periskope_connector.ai import classifier
from periskope_connector.system import insert_as_system
from periskope_connector.text_rules import skip_fields


def _tag(name, description="a test tag", **kwargs):
	doc = frappe.get_doc({"doctype": "Periskope Tag", "tag_name": name, "description": description, **kwargs})
	doc.insert(ignore_permissions=True)
	return doc


class TestTags(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_tags_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_tags_test")
		install._ensure_tags()

	def test_the_starting_set_is_created_once(self):
		self.assertEqual(set(tags.names()), {name for name, _ in tags.DEFAULT_TAGS} | set(tags.names()))
		before = frappe.db.count("Periskope Tag")
		install._ensure_tags()
		self.assertEqual(frappe.db.count("Periskope Tag"), before)
		self.assertEqual(tags.default_tag(), "general")

	def test_a_new_tag_reaches_the_prompt_the_schema_and_the_reply_format(self):
		_tag("price_negotiation", "the customer pushes back on the price")
		self.assertIn(
			"price_negotiation: the customer pushes back on the price", classifier.build_system_prompt()
		)
		self.assertIn("price_negotiation", classifier.build_schema()["properties"]["category"]["enum"])
		self.assertIn('"price_negotiation"', classifier.json_reply_instructions())

	def test_an_inactive_tag_is_no_longer_offered(self):
		frappe.db.set_value("Periskope Tag", "sample_request", "active", 0)
		self.assertNotIn("sample_request", tags.names())
		self.assertNotIn("sample_request", classifier.build_system_prompt())

	def test_an_unknown_answer_from_the_ai_falls_back_to_the_default_tag(self):
		doc = insert_as_system({"doctype": "Periskope Message", "message_id": "tag_fallback", "body": "hi"})
		classifier.apply_result(doc, {"category": "made_up", "severity": "Low", "sentiment": "Neutral"})
		self.assertEqual(doc.ai_category, "general")
		frappe.db.set_value("Periskope Tag", "general", "is_default", 0)
		frappe.db.set_value("Periskope Tag", "delivery_delay", "is_default", 1)
		classifier.apply_result(doc, {"category": "made_up"})
		self.assertEqual(doc.ai_category, "delivery_delay")

	def test_skipped_messages_get_the_default_tag(self):
		frappe.db.set_value("Periskope Tag", "general", "is_default", 0)
		frappe.db.set_value("Periskope Tag", "delivery_delay", "is_default", 1)
		self.assertEqual(skip_fields("Too short to tag")["ai_category"], "delivery_delay")

	def test_the_name_must_be_lowercase_with_underscores(self):
		for bad in ("Bad Name", "1abc", "UPPER", "with-dash"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				_tag(bad)

	def test_only_one_tag_is_the_default(self):
		_tag("new_default", is_default=1)
		self.assertEqual(frappe.db.count("Periskope Tag", {"is_default": 1}), 1)
		self.assertEqual(tags.default_tag(), "new_default")

	def test_the_default_tag_cannot_be_deactivated_or_deleted(self):
		general = frappe.get_doc("Periskope Tag", "general")
		general.active = 0
		with self.assertRaises(frappe.ValidationError):
			general.save()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Periskope Tag", "general")

	def test_the_last_active_tag_cannot_be_deactivated(self):
		for name in tags.names():
			if name != "general":
				frappe.db.set_value("Periskope Tag", name, "active", 0)
		general = frappe.get_doc("Periskope Tag", "general")
		general.active = 0
		with self.assertRaises(frappe.ValidationError):
			general.save()

	def test_a_manager_can_only_correct_to_an_active_tag(self):
		from periskope_connector.api import settings as settings_api

		frappe.db.set_value("Periskope Tag", "sample_request", "active", 0)
		with self.assertRaises(frappe.ValidationError):
			settings_api.set_correct_category("any", "sample_request")

	def test_the_patch_removes_the_category_list_from_saved_instructions(self):
		from periskope_connector.patches import use_tags_in_ai_instructions as patch_module

		old = (
			"Rules:\n- category is exactly one of:\n  - general: anything else.\n"
			'- Messages labelled "Rep" are sent by us.'
		)
		frappe.db.set_single_value("Periskope Settings", "ai_instructions", old)
		patch_module.execute()
		new = frappe.db.get_single_value("Periskope Settings", "ai_instructions")
		self.assertNotIn("general: anything else", new)
		self.assertIn("listed under Categories below", new)
		self.assertIn('Messages labelled "Rep"', new)
