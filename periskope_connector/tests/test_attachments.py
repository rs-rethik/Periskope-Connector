"""Tests for attachments: files are saved on the lead or deal, and images and PDFs are read by the AI."""

import base64
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import attachments, linking, messages
from periskope_connector.ai import classifier
from periskope_connector.tests.utils import event, isolate, settings


def _pdf() -> bytes:
	"""A real one-page PDF: Frappe checks a PDF before it saves one."""
	from io import BytesIO

	from pypdf import PdfWriter

	writer, out = PdfWriter(), BytesIO()
	writer.add_blank_page(width=72, height=72)
	writer.write(out)
	return out.getvalue()


PDF = _pdf()
NUMBER = "919600000431"


def _media(mimetype="application/pdf", size=None, filename="PO 1182.pdf"):
	return {
		"path": "https://api.periskope.app/app/media/x/1.pdf",
		"mimetype": mimetype,
		"size": len(PDF) if size is None else size,
		"filename": filename,
	}


class TestAttachments(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_attachments_test")
		settings(enabled=1, test_mode=0, ai_provider="Direct API", ai_model="claude-haiku-4-5")
		for p in (
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
			patch.object(attachments, "download", lambda url, limit=attachments.MAX_BYTES: PDF),
		):
			p.start()
			self.addCleanup(p.stop)
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Files", "mobile_no": f"+{NUMBER}"})
		lead.flags.ignore_mandatory = True
		self.lead = lead.insert(ignore_permissions=True).name
		self.n = 0

	def _message(self, body="", message_type="document", **media):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"att_{self.n}_{frappe.generate_hash(length=5)}",
				chat_id=f"{NUMBER}@c.us",
				sender_phone=f"{NUMBER}@c.us",
				body=body,
				message_type=message_type,
				media=_media(**media),
			)
		)

	def _files(self, doctype, name):
		return frappe.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": name},
			fields=["name", "file_name", "is_private", "file_url"],
		)

	def test_a_file_is_saved_and_shown_on_the_lead_once(self):
		name = self._message()
		saved = attachments.save(name)
		self.assertTrue(saved)
		on_message = self._files("Periskope Message", name)
		on_lead = self._files("CRM Lead", self.lead)
		self.assertEqual((on_message[0].file_name, on_message[0].is_private), ("PO 1182.pdf", 1))
		self.assertEqual(len(on_lead), 1)
		self.assertEqual(attachments._read(on_lead[0].name), PDF)  # the same bytes, not spoilt
		self.assertEqual(attachments.save(name), saved)  # not twice
		attachments.attach_to_record(name)
		self.assertEqual(len(self._files("CRM Lead", self.lead)), 1)

	def test_a_file_too_large_is_left_as_a_link(self):
		name = self._message(size=attachments.MAX_BYTES + 1)
		self.assertIsNone(attachments.save(name))
		self.assertEqual(self._files("Periskope Message", name), [])

	def test_a_bare_pdf_or_photo_is_read_by_the_ai_and_other_files_are_not(self):
		pdf = frappe.get_doc("Periskope Message", self._message())
		self.assertEqual(pdf.classification_status, "Pending")  # no caption, still analysed
		photo = frappe.get_doc(
			"Periskope Message",
			self._message(message_type="image", mimetype="image/jpeg", filename="IMG.jpg"),
		)
		self.assertEqual(photo.classification_status, "Pending")
		video = frappe.get_doc("Periskope Message", self._message(message_type="video", mimetype="video/mp4"))
		self.assertEqual(
			(video.classification_status, video.classification_error), ("Skipped", "Not a text message")
		)
		big = frappe.get_doc(
			"Periskope Message",
			self._message(message_type="image", mimetype="image/png", size=6 * 1024 * 1024),
		)
		self.assertFalse(attachments.readable(big))

	def test_only_a_claude_model_on_the_direct_api_reads_the_file(self):
		doc = frappe.get_doc("Periskope Message", self._message())
		self.assertTrue(classifier.can_read_attachment(doc, SimpleNamespace(ai_model="claude-haiku-4-5")))
		self.assertFalse(classifier.can_read_attachment(doc, SimpleNamespace(ai_model="glm-4.6")))
		self.assertFalse(
			classifier.can_read_attachment(
				doc, SimpleNamespace(ai_model="claude-haiku-4-5", ai_provider="Jarvis")
			)
		)

	def test_the_ai_gets_the_document_and_its_reading_lands_on_the_timeline(self):
		name = self._message()
		attachments.save(name)
		doc = frappe.get_doc("Periskope Message", name)
		seen = {}
		answer = {
			"category": "order_or_enquiry",
			"sentiment": "Neutral",
			"severity": "Low",
			"summary": "A purchase order",
			"confidence": 90,
			"needs_reply": True,
			"on_timeline": True,
			"attachment_summary": "Purchase order 1182 for 500 m of poly spandex, due 30 Oct.",
			"tasks": [],
		}

		def create(**request):
			seen.update(request)
			return SimpleNamespace(
				stop_reason="end_turn",
				usage=None,
				content=[SimpleNamespace(type="text", text=frappe.as_json(answer))],
			)

		client = SimpleNamespace(messages=SimpleNamespace(create=create))
		with (
			patch.object(classifier, "_client", return_value=client),
			patch("periskope_connector.ai.analyse.queue", lambda *a, **k: None),
		):
			classifier.classify(name)
		block = seen["messages"][0]["content"][0]
		self.assertEqual((block["type"], block["source"]["media_type"]), ("document", "application/pdf"))
		self.assertEqual(base64.b64decode(block["source"]["data"]), PDF)
		self.assertIn(
			"Attached to this message: document PO 1182.pdf", seen["messages"][0]["content"][1]["text"]
		)
		doc.reload()
		self.assertEqual(doc.attachment_summary, answer["attachment_summary"])
		comment = frappe.db.get_value("Comment", doc.timeline_comment, "content")
		self.assertIn("Purchase order 1182", comment)

	def _classify(self, name, answer):
		seen = {}

		def create(**request):
			seen.update(request)
			return SimpleNamespace(
				stop_reason="end_turn",
				usage=None,
				content=[SimpleNamespace(type="text", text=frappe.as_json(answer))],
			)

		client = SimpleNamespace(messages=SimpleNamespace(create=create))
		with (
			patch.object(classifier, "_client", return_value=client),
			patch("periskope_connector.ai.analyse.queue", lambda *a, **k: None),
		):
			classifier.classify(name)
		return seen["messages"][0]["content"][1]["text"]

	def test_the_ai_says_why_a_file_is_not_on_a_lead_or_deal(self):
		answer = {
			"category": "order_or_enquiry",
			"summary": "A supplier's invoice",
			"attachment_summary": "Invoice 77 from a yarn supplier.",
			"attachment_not_on_crm": "A supplier sent its own invoice; it is not a customer's, so there is no lead.",
		}
		other = "919600000432"
		name = messages.create_message(
			event(
				message_id=f"att_x_{frappe.generate_hash(length=5)}",
				chat_id=f"{other}@c.us",
				sender_phone=f"{other}@c.us",
				message_type="image",
				media=_media(mimetype="image/jpeg", filename="IMG.jpg"),
			)
		)
		prompt = self._classify(name, answer)
		self.assertIn("not linked to any lead or deal (no lead or deal in the CRM has this phone", prompt)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", name, "attachment_not_on_crm"),
			answer["attachment_not_on_crm"],
		)

		# Once the number is in the CRM the message links, the file goes there and the reason is cleared.
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Later", "mobile_no": f"+{other}"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		linking.link_message(name)
		self.assertFalse(frappe.db.get_value("Periskope Message", name, "attachment_not_on_crm"))

	def test_a_linked_file_keeps_no_reason(self):
		name = self._message()
		prompt = self._classify(name, {"category": "order_or_enquiry", "attachment_not_on_crm": "Anything"})
		self.assertIn(f"linked to CRM Lead {self.lead}", prompt)
		self.assertFalse(frappe.db.get_value("Periskope Message", name, "attachment_not_on_crm"))


class TestCleanupPatch(FrappeTestCase):
	def test_the_cleanup_can_run_again(self):
		from periskope_connector.patches import remove_rule_doctypes

		remove_rule_doctypes.execute()
		for doctype in remove_rule_doctypes.DOCTYPES:
			self.assertFalse(frappe.db.exists("DocType", doctype))
