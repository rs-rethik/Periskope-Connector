"""Tests for the festival look (ai/festival.py): the AI's web-searched answer, kept for the day."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import branding
from periskope_connector.ai import assistant, classifier, festival
from periskope_connector.api import today as today_api
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


def reply(answer: dict, stop_reason: str = "end_turn"):
	return SimpleNamespace(
		stop_reason=stop_reason,
		content=[SimpleNamespace(type="text", text=json.dumps(answer))],
		usage=SimpleNamespace(input_tokens=10, output_tokens=10),
	)


DIWALI = {
	"festival": "Diwali",
	"look": "diwali",
	"greeting": "Happy Diwali to the whole team!",
	"confidence": "high",
	"source": "https://example.com/diwali",
}


class TestFestival(FrappeTestCase):
	def setUp(self):
		self._clear()
		themes = patch.object(branding, "festival_themes", return_value=True)
		themes.start()
		self.addCleanup(themes.stop)
		self.settings = SimpleNamespace(
			ai_model="claude-sonnet-5-5", ai_provider=None, ai_business_context="A textile mill."
		)

	def tearDown(self):
		self._clear()

	def _clear(self):
		frappe.cache().delete_value(festival.cache_key())
		frappe.cache().delete_value(festival.cache_key() + ":tried")

	def _refresh(self, *responses):
		client = MagicMock()
		client.messages.create.side_effect = list(responses)
		with (
			patch.object(classifier, "get_settings", return_value=self.settings),
			patch.object(assistant, "is_ready", return_value=True),
			patch.object(classifier, "_client", return_value=client),
			patch.object(classifier, "_record_usage"),
		):
			festival.refresh()
		return client

	def test_a_confident_festival_is_shown_on_today_and_asked_only_once_a_day(self):
		client = self._refresh(reply(DIWALI))
		tool = client.messages.create.call_args.kwargs["tools"][0]
		self.assertEqual(tool["type"], "web_search_20260209")
		self.assertEqual(today_api.get_today()["festival"]["greeting"], DIWALI["greeting"])
		again = self._refresh(reply(DIWALI))
		again.messages.create.assert_not_called()

	def test_an_ordinary_or_unsure_day_shows_nothing(self):
		self._refresh(reply({**DIWALI, "confidence": "medium"}))
		self.assertIsNone(festival.current())
		self._clear()
		self._refresh(reply({"festival": None, "look": "festive", "greeting": "", "confidence": "high"}))
		self.assertIsNone(festival.current())

	def test_an_unknown_look_falls_back_and_a_paused_search_is_resumed(self):
		paused = SimpleNamespace(
			stop_reason="pause_turn", content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1)
		)
		client = self._refresh(paused, reply({**DIWALI, "look": "fireworks"}))
		self.assertEqual(client.messages.create.call_count, 2)
		self.assertEqual(festival.current()["look"], "festive")

	def test_an_older_model_uses_the_basic_search_and_a_failure_waits_before_retrying(self):
		self.settings.ai_model = "claude-haiku-4-5"
		client = self._refresh(Exception("search is off"))
		self.assertEqual(client.messages.create.call_args.kwargs["tools"][0]["type"], "web_search_20250305")
		self.assertIsNone(festival.current())
		self._refresh(reply(DIWALI)).messages.create.assert_not_called()

	def test_no_web_search_without_a_claude_key(self):
		self.settings.ai_provider = classifier.PROVIDER_JARVIS
		self._refresh(reply(DIWALI)).messages.create.assert_not_called()
