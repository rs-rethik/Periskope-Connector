"""What a shared location, contact card or poll holds, read from the message's webhook payload and
kept on the message (shared_details), so the inbox can show it the way WhatsApp does. Periskope does
not document these fields (it passes on WhatsApp Web's), so each is read defensively and anything
missing is left out."""

import json
import re

KINDS = ("location", "contact", "poll")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def details(kind: str, data: dict | str | None) -> dict | None:
	"""The parts of a location, contact or poll message worth showing; None when there are none.
	`data` is the webhook's message data, or the raw_payload it was saved as."""
	if kind not in KINDS or not data:
		return None
	if isinstance(data, str):
		try:
			data = json.loads(data)
		except ValueError:
			return None
	if not isinstance(data, dict):
		return None
	found = {"location": _location, "contact": _contacts, "poll": _poll}[kind](data)
	return found or None


def _location(data: dict) -> dict:
	loc = data.get("location") or {}
	if isinstance(loc, str):  # "lat,lng"
		parts = NUMBER.findall(loc)
		loc = {"latitude": parts[0], "longitude": parts[1]} if len(parts) >= 2 else {}
	if not isinstance(loc, dict):
		return {}
	options = loc.get("options") if isinstance(loc.get("options"), dict) else {}
	lat = _number(loc.get("latitude", loc.get("lat")))
	lng = _number(loc.get("longitude", loc.get("lng")))
	name = loc.get("name") or options.get("name") or ""
	address = loc.get("address") or options.get("address") or ""
	if not (name or address) and loc.get("description"):
		# WhatsApp Web joins them as "name\naddress".
		name, _, address = str(loc["description"]).partition("\n")
	out = {"name": str(name).strip(), "address": str(address).strip()}
	if lat is not None and lng is not None:
		out.update(latitude=lat, longitude=lng)
	url = loc.get("url") or options.get("url")
	if isinstance(url, str) and url.startswith(("http://", "https://")):
		out["url"] = url
	return {k: v for k, v in out.items() if v not in ("", None)}


def _number(value) -> float | None:
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _contacts(data: dict) -> dict:
	cards = data.get("vcards") or []
	if isinstance(cards, str):
		cards = [cards]
	contacts = [c for c in (_vcard(card) for card in cards if isinstance(card, str)) if c]
	return {"contacts": contacts} if contacts else {}


def _vcard(card: str) -> dict:
	"""Name and phone numbers of one vCard ("FN:..", "TEL;waid=91..:+91 ..")."""
	name, phones = "", []
	for line in card.replace("\r", "").split("\n"):
		key, _, value = line.partition(":")
		field = key.split(";")[0].upper()
		if field == "FN":
			name = value.strip()
		elif field == "TEL" and value.strip():
			waid = re.search(r"waid=(\d+)", key)
			phones.append({"number": value.strip(), "whatsapp": waid.group(1) if waid else ""})
	return {"name": name, "phones": phones} if (name or phones) else {}


def _poll(data: dict) -> dict:
	info = data.get("poll_info") or {}
	if not isinstance(info, dict):
		return {}
	options = []
	for option in info.get("pollOptions") or info.get("options") or []:
		label = option.get("name") if isinstance(option, dict) else option
		if isinstance(label, str) and label.strip():
			options.append(label.strip())
	results = data.get("poll_results") if isinstance(data.get("poll_results"), dict) else {}
	votes = {o: len(results.get(o) or []) for o in options if isinstance(results.get(o), list)}
	out = {"question": str(info.get("pollName") or info.get("name") or "").strip(), "options": options}
	if votes:
		out["votes"] = votes
	return out if (out["question"] or options) else {}


def from_row(row) -> dict | None:
	"""A message row's saved shared_details for the browser (a JSON string, or parsed already)."""
	value = row.pop("shared_details", None)
	if isinstance(value, str):
		try:
			return json.loads(value) if value else None
		except ValueError:
			return None
	return value or None
