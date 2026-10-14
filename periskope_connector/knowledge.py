"""The knowledge base the auto reply answers from.

Entries are question and answer pairs. The agent mines them from conversations where a rep answered
a customer's question; the team can also write them by hand. A mined entry starts as Suggested and
is used for replies only after a manager approved it (see suggestions.py), because its text goes to
customers. Repeats of the same question add to the entry instead of making a new one.
"""

import re

import frappe
from frappe.utils import add_days, now_datetime

from periskope_connector import suggestions
from periskope_connector.ai import assistant
from periskope_connector.system import insert_as_system

SAME_QUESTION = 0.6  # word overlap above which two questions are the same
CHANGED_ANSWER = 0.4  # answer overlap below which a repeat gives a different answer
MIN_CONFIDENCE = 60
STALE_DAYS = 90
STOP = {
	"the",
	"a",
	"an",
	"is",
	"are",
	"of",
	"to",
	"for",
	"and",
	"do",
	"you",
	"what",
	"your",
	"in",
	"on",
	"i",
	"we",
	"can",
	"have",
	"any",
	"me",
	"please",
}

SYSTEM_PROMPT = """You build a knowledge base for a company's WhatsApp customer chats. You receive conversations between customers and the company's reps (and sometimes an automatic assistant, which you must ignore).

Extract only question and answer pairs where a rep gave a clear, reusable answer to a question another customer could also ask: product facts, availability, minimum order, lead time, delivery or payment terms, how a process works, company details.
Do NOT extract: anything specific to one order, person, invoice or complaint; negotiated or one-off prices; greetings; anything the rep was unsure about; personal data (names, phone numbers, addresses). Write the question and answer in plain, general wording, in the language the rep used. If nothing qualifies, return an empty list.
confidence is 0-100: how sure you are the answer is correct and reusable. evidence_chat is the number of the conversation it came from."""

SCHEMA = {
	"type": "object",
	"properties": {
		"entries": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"question": {"type": "string"},
					"answer": {"type": "string"},
					"keywords": {"type": "string"},
					"confidence": {"type": "integer"},
					"evidence_chat": {"type": "integer"},
				},
				"required": ["question", "answer", "keywords", "confidence", "evidence_chat"],
				"additionalProperties": False,
			},
		}
	},
	"required": ["entries"],
	"additionalProperties": False,
}


def words(text: str) -> set[str]:
	return {w for w in re.findall(r"\w+", (text or "").lower()) if w not in STOP and len(w) > 1}


def similarity(a: str, b: str) -> float:
	wa, wb = words(a), words(b)
	return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _similar(question: str, entries: list) -> object | None:
	best = max(entries, key=lambda e: similarity(question, e.question), default=None)
	return best if best and similarity(question, best.question) >= SAME_QUESTION else None


def record(item: dict, evidence: list[str]) -> str:
	"""Save one mined pair. Returns "added", "seen" (a repeat of an entry) or "changed" or "" (dropped)."""
	question, answer = (item.get("question") or "").strip(), (item.get("answer") or "").strip()
	if not question or not answer or (item.get("confidence") or 0) < MIN_CONFIDENCE:
		return ""
	entries = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": ["!=", "Retired"]},
		fields=["name", "question", "answer", "status", "evidence_count"],
	)
	match = _similar(question, entries)
	if match:
		frappe.db.set_value(
			"Periskope Knowledge Entry",
			match.name,
			{"evidence_count": (match.evidence_count or 1) + 1, "last_seen": now_datetime()},
			update_modified=False,
		)
		if match.status == "Approved" and similarity(answer, match.answer) < CHANGED_ANSWER:
			made = suggestions.create(
				"Knowledge Entry",
				"Modify",
				f"New answer for: {match.question}"[:140],
				reason="Reps are now answering this differently from the saved answer.",
				target=match.name,
				payload={"answer": answer},
				current={"answer": match.answer},
				evidence=evidence,
			)
			return "changed" if made else "seen"
		return "seen"

	entry = insert_as_system(
		{
			"doctype": "Periskope Knowledge Entry",
			"question": question,
			"answer": answer,
			"keywords": item.get("keywords") or "",
			"status": "Suggested",
			"source": "AI Learned",
			"confidence": item.get("confidence"),
			"evidence_count": 1,
			"last_seen": now_datetime(),
		}
	)
	suggestions.create(
		"Knowledge Entry",
		"Add",
		f"Add to the knowledge base: {question}"[:140],
		reason="A rep gave this answer to a customer; it can answer the same question next time.",
		target=entry.name,
		payload={"question": question, "answer": answer, "keywords": item.get("keywords") or ""},
		evidence=evidence,
	)
	return "added"


def mine(transcript: str, chats: dict[int, list[str]]) -> dict:
	"""One AI call over a batch of conversations. `chats` maps the conversation number in the
	transcript to its message names (the evidence)."""
	result = assistant.complete_json(SYSTEM_PROMPT, transcript, SCHEMA)
	counts = {"added": 0, "seen": 0, "changed": 0}
	for item in result.get("entries") or []:
		outcome = record(item, (chats.get(item.get("evidence_chat")) or [])[:5])
		if outcome:
			counts[outcome] += 1
	return counts


def flag_stale() -> int:
	"""Suggest retiring approved entries nobody asked for and no rep repeated in a long time."""
	cutoff = add_days(now_datetime(), -STALE_DAYS)
	rows = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": "Approved", "creation": ["<", cutoff]},
		or_filters=[["last_seen", "<", cutoff], ["last_seen", "is", "not set"]],
		fields=["name", "question", "last_used"],
	)
	made = 0
	for row in rows:
		if row.last_used and row.last_used > cutoff:
			continue
		if suggestions.create(
			"Knowledge Entry",
			"Remove",
			f"Retire: {row.question}"[:140],
			reason=f"Not used or repeated by reps for over {STALE_DAYS} days.",
			target=row.name,
		):
			made += 1
	return made


def find(text: str, limit: int = 5) -> list:
	"""Approved entries that match a customer's text, best first (for the auto reply)."""
	entries = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": "Approved"},
		fields=["name", "question", "answer", "keywords"],
	)
	scored = []
	for entry in entries:
		score = max(similarity(text, entry.question), similarity(text, entry.keywords or "") * 0.8)
		overlap = len(words(text) & (words(entry.question) | words(entry.keywords or "")))
		if score >= 0.15 or overlap >= 2:
			scored.append((score + overlap * 0.01, entry))
	return [e for _score, e in sorted(scored, key=lambda s: s[0], reverse=True)[:limit]]
