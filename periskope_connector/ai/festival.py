"""Festival look for the app, decided by the AI with a web search and no festival table.

Once a day (the hourly job does nothing after the day's answer is in) the AI searches the web for what
today is in the company's country and replies with the festival, one of the looks the app can draw, a
short greeting and how sure it is. Only a confident answer is shown, and only while Settings > Branding
has festival themes on. The answer is kept in the cache for the day, so opening the app never calls the
AI. Without an Anthropic key (Jarvis, other providers) there is no web search and no festival look."""

import time

import frappe
from frappe.utils import format_date, get_system_timezone, getdate, today

from periskope_connector import access, branding
from periskope_connector.ai import assistant, classifier

LOOKS = (
	"diwali",
	"pongal",
	"christmas",
	"new_year",
	"tamil_new_year",
	"ayudha_pooja",
	"holi",
	"eid",
	"national",
	"festive",
)
KEEP_SECONDS = 36 * 3600
RETRY_SECONDS = 3 * 3600  # a failed check waits this long before the next try
MAX_SEARCHES = 3
MAX_CONTINUATIONS = 3
# Models with the dynamic-filtering web search; other Claude models use the basic one.
NEW_SEARCH_MODELS = (
	"claude-opus-5-5",
	"claude-opus-5",
	"claude-opus-4-8",
	"claude-opus-4-7",
	"claude-opus-4-6",
	"claude-sonnet-5-5",
	"claude-sonnet-5",
	"claude-sonnet-4-6",
)

SYSTEM = """You decide whether today is a festival or a national celebration for a company's staff, so \
their work app can show a festive look and a greeting. Search the web to confirm what today's date is in \
the company's country and region: festivals move with the lunar calendar, so never answer from memory.

Count only days most of the staff would celebrate or that are public holidays there (for example Diwali, \
Pongal, Christmas, New Year, Tamil New Year, Ayudha Pooja, Holi, Eid, Independence Day). Ordinary \
observances, awareness days and days before or after a festival do not count.

Pick the look closest to the festival from: {looks}. "national" is for national days, "festive" for any \
other festival. Write a warm greeting of at most 12 words for the staff in English.

Reply with ONLY one JSON object, no commentary and no code fences:
{{"festival": "<name, or null when today is not a festival>", "look": "<one look>", \
"greeting": "<greeting, or empty>", "confidence": "high|medium|low", "source": "<URL that confirms it>"}}"""


def cache_key(day=None) -> str:
	return f"periskope:festival:{day or today()}"


@frappe.whitelist()
def get_festival() -> dict | None:
	"""Today's festival look, or None. Reads only the cache; the hourly job fills it."""
	access.require_inbox_user()
	return current()


def current() -> dict | None:
	if not branding.festival_themes():
		return None
	found = frappe.cache().get_value(cache_key(), expires=True)
	return found if found and found.get("show") else None


def refresh(force: bool = False) -> dict | None:
	"""Ask the AI about today once a day (hourly job). Never raises."""
	if not branding.festival_themes():
		return None
	cache = frappe.cache()
	key = cache_key()
	if not force and (cache.get_value(key, expires=True) or cache.get_value(key + ":tried", expires=True)):
		return current()
	settings = classifier.get_settings()
	model = settings.ai_model or classifier.DEFAULT_MODEL
	if (
		classifier.uses_jarvis(settings)
		or not classifier.is_claude(model)
		or not assistant.is_ready(settings)
	):
		return None
	try:
		answer = ask(settings, model)
	except Exception:
		cache.set_value(key + ":tried", 1, expires_in_sec=RETRY_SECONDS)
		frappe.log_error(title="Festival check failed", message=frappe.get_traceback())
		return None
	cache.set_value(key, answer, expires_in_sec=KEEP_SECONDS)
	return current()


def ask(settings, model: str, day=None) -> dict:
	country = frappe.db.get_single_value("System Settings", "country") or ""
	timezone = get_system_timezone()
	day = getdate(day or today())
	context = (settings.ai_business_context or "").strip()
	user = f"Today is {format_date(day, 'EEEE, d MMMM yyyy')} in {timezone}. Country: {country or 'unknown'}."
	if context:
		user += f"\nAbout the company:\n{context[:1500]}"

	search = {
		"type": "web_search_20260209" if model.startswith(NEW_SEARCH_MODELS) else "web_search_20250305",
		"name": "web_search",
		"max_uses": MAX_SEARCHES,
	}
	if timezone:
		search["user_location"] = {"type": "approximate", "timezone": timezone}

	messages = [{"role": "user", "content": user}]
	client = classifier._client(settings)
	started = time.monotonic()
	for _attempt in range(MAX_CONTINUATIONS + 1):
		response = client.messages.create(
			model=model,
			max_tokens=2000,
			system=SYSTEM.format(looks=", ".join(LOOKS)),
			tools=[search],
			messages=messages,
		)
		classifier._record_usage(None, settings, model, response, int((time.monotonic() - started) * 1000))
		if response.stop_reason != "pause_turn":
			break
		# a long server-side search: send the turn back and the server carries on
		messages = [messages[0], {"role": "assistant", "content": response.content}]
	if response.stop_reason in ("refusal", "max_tokens", "pause_turn"):
		frappe.throw(f"The AI stopped early: {response.stop_reason}")
	texts = [b.text for b in response.content if b.type == "text"]
	return judge(classifier.parse_json_reply("".join(texts)), day)


def judge(reply: dict, day) -> dict:
	"""Keep only a confident festival with a look the app knows."""
	festival = (reply.get("festival") or "").strip()
	if festival.lower() in ("null", "none"):
		festival = ""
	look = reply.get("look") if reply.get("look") in LOOKS else "festive"
	greeting = (reply.get("greeting") or "").strip()[:120] or (f"Happy {festival}!" if festival else "")
	return {
		"date": str(day),
		"festival": festival or None,
		"look": look,
		"greeting": greeting,
		"confidence": reply.get("confidence"),
		"source": reply.get("source"),
		"show": bool(festival) and reply.get("confidence") == "high",
	}


@frappe.whitelist(methods=["POST"])
def check_again() -> dict | None:
	"""A manager asks the AI again now (for instance after changing the key)."""
	access.require_inbox_user()
	if not access.is_manager():
		frappe.throw("Only managers can do this.", frappe.PermissionError)
	frappe.cache().delete_value(cache_key() + ":tried")
	refresh(force=True)
	return frappe.cache().get_value(cache_key(), expires=True)


def preview(look: str, festival: str = "", greeting: str = "") -> None:
	"""bench console: show a look today without the AI, to see how it draws."""
	frappe.cache().set_value(
		cache_key(),
		judge(
			{"festival": festival or look.title(), "look": look, "greeting": greeting, "confidence": "high"},
			today(),
		),
		expires_in_sec=KEEP_SECONDS,
	)
