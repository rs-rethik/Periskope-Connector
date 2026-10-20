"""Images and documents from WhatsApp, kept in Frappe and read by the AI.

1. Saving: every attachment (image, document, video, voice note) is downloaded from its Periskope
   media link (a redirect to a signed download, no key needed) and saved as a private File on the
   message. Once the message is linked to a lead or deal, the same file is attached there as well, so
   it shows in the CRM's Attachments tab. A background job; a file over MAX_BYTES is left as a link.
2. Reading: an image or a PDF goes to Claude with the message (content_block), so the analysis
   says what it is (an invoice, a quotation, a purchase order, a screenshot) and its key facts
   (attachment_summary), and its tasks and escalations follow the same flow as text
   (decisions.py). Only with a Claude model on the Direct API; other providers see the caption only.
"""

import base64
import mimetypes

import frappe
import requests

MAX_BYTES = 15 * 1024 * 1024  # saved in Frappe
TIMEOUT = 60
KINDS = ("image", "document", "video", "audio")
# What Claude can read, and how large: images up to 5 MB, PDFs up to 10 MB (a few dozen pages).
READABLE = {
	"image/jpeg": ("image", 5 * 1024 * 1024),
	"image/png": ("image", 5 * 1024 * 1024),
	"image/gif": ("image", 5 * 1024 * 1024),
	"image/webp": ("image", 5 * 1024 * 1024),
	"application/pdf": ("document", 10 * 1024 * 1024),
}
FIELDS = [
	"name",
	"message_type",
	"media_path",
	"media_filename",
	"media_mimetype",
	"media_size",
	"attachment_file",
	"crm_lead",
	"crm_deal",
]


def has_file(doc) -> bool:
	return (doc.message_type or "") in KINDS and (doc.media_path or "").startswith(("http://", "https://"))


def _mime(doc) -> str:
	return (doc.media_mimetype or "").split(";")[0].strip().lower()


def readable(doc) -> bool:
	"""An image or PDF the AI can read (by type and size)."""
	kind = READABLE.get(_mime(doc))
	return has_file(doc) and bool(kind) and (doc.media_size or 0) <= kind[1]


def queue(message: str):
	frappe.enqueue(
		"periskope_connector.attachments.save",
		message=message,
		queue="long",
		job_id=f"periskope_attachment::{message}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def download(url: str, limit: int = MAX_BYTES) -> bytes:
	with requests.get(url, stream=True, timeout=TIMEOUT, allow_redirects=True) as response:
		response.raise_for_status()
		data = b""
		for chunk in response.iter_content(64 * 1024):
			data += chunk
			if len(data) > limit:
				raise ValueError("The attachment is too large")
		return data


def _file_name(doc) -> str:
	name = (doc.media_filename or "").strip() or doc.name
	if "." not in name[-6:]:
		name += mimetypes.guess_extension(_mime(doc)) or ""
	return name


def _record(doc) -> tuple[str, str] | None:
	if doc.crm_deal:
		return "CRM Deal", doc.crm_deal
	if doc.crm_lead:
		return "CRM Lead", doc.crm_lead
	return None


def save(message: str) -> str | None:
	"""Background job: save the attachment of a message as a private File. Returns the File name."""
	doc = frappe.db.get_value("Periskope Message", message, FIELDS, as_dict=True)
	if not doc or not has_file(doc) or doc.attachment_file:
		return doc.attachment_file if doc else None
	if (doc.media_size or 0) > MAX_BYTES:
		return None
	try:
		content = download(doc.media_path)
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": _file_name(doc),
				"content": content,
				"is_private": 1,
				"attached_to_doctype": "Periskope Message",
				"attached_to_name": message,
			}
		)
		file.flags.ignore_permissions = True
		file.insert()
	except Exception:
		frappe.log_error(title=f"Periskope: could not save the attachment of message {message}")
		return None
	frappe.db.set_value("Periskope Message", message, "attachment_file", file.name, update_modified=False)
	doc.attachment_file = file.name
	attach_to_record(message, doc)
	frappe.db.commit()
	return file.name


def attach_to_record(message: str, doc=None) -> str | None:
	"""Show the saved file in the Attachments of the message's lead or deal. Never raises."""
	try:
		doc = doc or frappe.db.get_value("Periskope Message", message, FIELDS, as_dict=True)
		record = doc and _record(doc)
		if not record or not doc.attachment_file:
			return None
		source = frappe.db.get_value("File", doc.attachment_file, ["file_name", "content_hash"], as_dict=True)
		if not source:
			return None
		existing = frappe.db.get_value(
			"File",
			{
				"content_hash": source.content_hash,
				"attached_to_doctype": record[0],
				"attached_to_name": record[1],
			},
		)
		if existing:
			return existing
		# The bytes themselves, not the file's address: Frappe v16 copies an address by reading it as
		# text, which spoils a PDF or a photo. (v15 shares the stored file when the content is the same.)
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": source.file_name,
				"content": _read(doc.attachment_file),
				"is_private": 1,
				"attached_to_doctype": record[0],
				"attached_to_name": record[1],
			}
		)
		file.flags.ignore_permissions = True
		file.insert()
		return file.name
	except Exception:
		frappe.log_error(title=f"Periskope: could not attach the file of message {message} to the CRM")
		return None


def _read(file_name: str) -> bytes:
	"""A saved file's bytes from disk (File.get_content() decodes text on Frappe v16)."""
	with open(frappe.get_doc("File", file_name).get_full_path(), "rb") as f:
		return f.read()


def _content(doc) -> bytes:
	if doc.attachment_file:
		try:
			return _read(doc.attachment_file)
		except Exception:
			pass
	return download(doc.media_path, READABLE[_mime(doc)][1])


def content_block(doc) -> dict | None:
	"""The image or PDF as a Claude content block, or None when it cannot be read."""
	if not readable(doc):
		return None
	try:
		data = base64.b64encode(_content(doc)).decode()
	except Exception:
		frappe.log_error(title=f"Periskope: could not read the attachment of message {doc.name}")
		return None
	kind = READABLE[_mime(doc)][0]
	return {"type": kind, "source": {"type": "base64", "media_type": _mime(doc), "data": data}}
