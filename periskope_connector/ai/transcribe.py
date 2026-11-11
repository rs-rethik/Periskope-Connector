"""Voice notes become text with Google Gemini, so the agent can tag them, answer them and learn from
them. Optional: only when a Gemini key is set in Periskope Settings > Advanced.

The audio comes from the Periskope media link (a redirect to a signed download, no key needed),
at most MAX_BYTES. The transcript is stored on the message; then the message goes on as if it had
arrived as text: tagged and answered by the auto reply. The tokens
count in the same monthly budget as every other AI call.
"""

import base64
import time

import frappe
import requests

from periskope_connector.system import insert_as_system

MAX_BYTES = 15 * 1024 * 1024
TIMEOUT = 60
MAX_ATTEMPTS = 3  # a voice note whose transcription fails is tried again (retry_failed) this often
RETRY_DAYS = 2
GAVE_UP = "Voice note could not be transcribed"
DEFAULT_MODEL = "gemini-2.5-flash"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
PROMPT = (
	"Transcribe this WhatsApp voice note word for word, in the language spoken (Tamil, English or "
	"Tanglish are common; write Tamil in Tamil script). Reply with the transcript only. If nothing "
	"can be heard, reply with an empty string."
)


def _key() -> str | None:
	return frappe.get_single("Periskope Settings").get_password("gemini_api_key", raise_exception=False)


def configured() -> bool:
	return bool(_key())


def queue(message: str):
	if configured():
		frappe.enqueue(
			"periskope_connector.ai.transcribe.run",
			message=message,
			queue="long",
			job_id=f"periskope_transcribe::{message}",
			deduplicate=True,
			enqueue_after_commit=True,
		)


def _download(url: str) -> tuple[bytes, str]:
	with requests.get(url, stream=True, timeout=TIMEOUT, allow_redirects=True) as response:
		response.raise_for_status()
		data = b""
		for chunk in response.iter_content(64 * 1024):
			data += chunk
			if len(data) > MAX_BYTES:
				raise ValueError("The voice note is too large to transcribe")
		return data, (response.headers.get("content-type") or "audio/ogg").split(";")[0]


def _audio(doc) -> tuple[bytes, str]:
	"""The voice note, from a fresh link when the saved one has expired."""
	try:
		return _download(doc.media_path)
	except requests.RequestException:
		from periskope_connector.api.message_actions import fresh_media_path

		fresh = doc.message_id and fresh_media_path(doc.message_id, doc.whatsapp_number or doc.org_phone)
		if not fresh or fresh == doc.media_path:
			raise
		frappe.db.set_value("Periskope Message", doc.name, "media_path", fresh, update_modified=False)
		return _download(fresh)


def retry_failed() -> int:
	"""Scheduler, every 30 minutes: transcribe again the customers' voice notes of the last RETRY_DAYS
	days that have no transcript yet (a Gemini error, a link that expired, a job that never ran), up to
	MAX_ATTEMPTS tries. Returns how many were queued."""
	if not configured():
		return 0
	now = frappe.utils.now_datetime()
	names = frappe.get_all(
		"Periskope Message",
		filters={
			"message_type": "audio",
			"direction": "Incoming",
			"transcript": ["is", "not set"],
			"media_path": ["like", "http%"],
			"transcript_attempts": ["<", MAX_ATTEMPTS],
			"creation": [
				"between",
				[frappe.utils.add_days(now, -RETRY_DAYS), frappe.utils.add_to_date(now, minutes=-30)],
			],
		},
		pluck="name",
		limit=100,
	)
	for name in names:
		queue(name)
	return len(names)


def _record_usage(message: str, model: str, usage: dict, started: float, error: str = ""):
	try:
		insert_as_system(
			{
				"doctype": "Periskope AI Usage",
				"message": message,
				"provider": "Gemini",
				"model": model,
				"input_tokens": usage.get("promptTokenCount") or 0,
				"output_tokens": usage.get("candidatesTokenCount") or 0,
				"duration_ms": int((time.monotonic() - started) * 1000),
				"status": "Error" if error else "OK",
				"error": error[:500],
			}
		)
	except Exception:
		frappe.log_error(title="Periskope: could not save the Gemini usage row")


def transcribe(audio: bytes, mime: str, message: str = "") -> str:
	"""The words of one voice note. Raises on failure."""
	settings = frappe.get_single("Periskope Settings")
	model = settings.gemini_model or DEFAULT_MODEL
	body = {
		"contents": [
			{
				"parts": [
					{"text": PROMPT},
					{"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}},
				]
			}
		]
	}
	started = time.monotonic()
	response = requests.post(
		ENDPOINT.format(model=model),
		headers={"x-goog-api-key": _key(), "Content-Type": "application/json"},
		json=body,
		timeout=TIMEOUT,
	)
	if not response.ok:
		_record_usage(message, model, {}, started, f"HTTP {response.status_code}")
		raise ValueError(f"Gemini returned HTTP {response.status_code}")
	data = response.json()
	_record_usage(message, model, data.get("usageMetadata") or {}, started)
	parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
	return " ".join((p.get("text") or "").strip() for p in parts).strip().strip('"')


def run(message: str) -> str:
	"""Background job for one voice note. Returns the transcript ("" when nothing was done)."""
	from periskope_connector import health
	from periskope_connector.ai import assistant
	from periskope_connector.autoreply import on_message
	from periskope_connector.setup_status import agent_on

	doc = frappe.get_doc("Periskope Message", message)
	from periskope_connector.chat import ai_off

	if ai_off(doc.chat_id):
		return ""
	if doc.transcript or doc.message_type != "audio" or not doc.media_path or not configured():
		return ""
	ready, reason = assistant.readiness()
	if not ready:
		health.notify_admin_once(reason)
		return ""
	try:
		audio, mime = _audio(doc)
		text = transcribe(audio, mime, message)
	except Exception:
		# Kept for retry_failed; after the last try it shows among the failed messages.
		attempts = (doc.transcript_attempts or 0) + 1
		values = {"transcript_attempts": attempts}
		if attempts >= MAX_ATTEMPTS and doc.direction == "Incoming":
			from periskope_connector.ai.classifier import MAX_ATTEMPTS as TAG_ATTEMPTS

			# Failed, but not tagged from "[voice note]" alone: a manager can retry it (failed list).
			values.update(
				classification_status="Failed",
				classification_error=GAVE_UP,
				classification_attempts=TAG_ATTEMPTS,
			)
		frappe.db.set_value("Periskope Message", message, values, update_modified=False)
		frappe.db.commit()  # a background job: the count must survive the failure
		health.log_once("Periskope: could not transcribe a voice note")
		return ""
	if not text:
		return ""
	values = {"transcript": text}
	if doc.direction == "Incoming" and agent_on():
		values.update(classification_status="Pending", classification_error="", classification_attempts=0)
	frappe.db.set_value("Periskope Message", message, values, update_modified=False)
	doc.transcript = text
	if values.get("classification_status") == "Pending":
		from periskope_connector.ai import classifier

		classifier.queue(message)
	on_message(doc)  # a voice note that asks something can now be answered
	frappe.db.commit()
	return text
