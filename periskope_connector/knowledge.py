"""The knowledge base the auto reply answers from.

Entries are question and answer pairs, arranged as a tree on the Knowledge Base canvas: Topic nodes
(Delivery) branch into Condition nodes (Customer is in Chennai, Otherwise) and end in Answer nodes.
An answer with no parent hangs straight under "Customer asks". The agent mines answers from
conversations where a rep answered a customer's question (kept only when the rep's own messages say
it, rep_said) and places each one in the tree: under the topic or condition it belongs to, or under a
new topic it proposes. The team can also draw the tree by hand on the canvas. A mined entry, a changed
answer and the retiring of a stale one are applied at once, no approval needed
(suggestions.AUTOMATIC_KINDS); the Periskope Suggestion row records each change, and the team corrects
the tree on the canvas. Repeats of the same question add to the entry instead of making a new one.
"""

import re

import frappe
from frappe.utils import add_days, now_datetime

from periskope_connector import suggestions
from periskope_connector.ai import assistant
from periskope_connector.system import insert_as_system

SAME_QUESTION = 0.6  # word overlap above which two questions are the same
CHANGED_ANSWER = 0.4  # answer overlap below which a repeat gives a different answer
SAME_ANSWER = 0.8  # answer overlap from which two answers say the same thing
MIN_CONFIDENCE = 60
SAID_BY_REP = 0.6  # share of a mined answer's words that the rep's own messages must contain
REP_LOOKBACK_DAYS = 2  # rep messages this long before the customer's question still count
MAX_OUTLINE_ANSWERS = 150  # existing answers shown to the AI, so it can tell new from changed
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
	"by",
}

SYSTEM_PROMPT = """You build a knowledge base for a company's WhatsApp customer chats. You receive conversations between customers and the company's reps (and sometimes an automatic assistant, which you must ignore).

Extract only question and answer pairs where a rep gave a clear, reusable answer to a question another customer could also ask: product facts, availability, minimum order, lead time, delivery or payment terms, how a process works, company details.
Do NOT extract: anything specific to one order, person, invoice or complaint; negotiated or one-off prices; greetings; anything the rep was unsure about; personal data (names, phone numbers, addresses). Write the question and answer in plain, general wording, in the language the rep used. If nothing qualifies, return an empty list.
confidence is 0-100: how sure you are the answer is correct and reusable. evidence_chat is the number of the conversation it came from.
You also get the current TREE of the knowledge base: topics, conditions and the answers under them, each with its [id]. The TREE is only for placing what you find: every entry must come from a rep's message in CONVERSATIONS; never copy an answer from the TREE.
- place: the id of the most specific topic or condition the answer belongs under. When no topic fits, leave place empty and give new_topic: a short name (1 to 3 words) for the subject, such as "Payment"; leave new_topic empty for a one-off answer.
- new_condition: when the answer holds only in a case that no existing condition under place covers (for example a delivery time for one city), that case in plain words, such as "Customer is in Bengaluru"; the answer then goes under a new condition. Otherwise empty.
- repeats: the id of an existing answer that says the same thing. replaces: the id of an existing answer that states the same fact with a different value (for example 2 kg free became 1 kg free, or who pays the courier changed); the rep's answer must be able to stand in for the old one completely. An answer that adds a different fact on the same subject (how to pay, next to when to pay) is new: leave both empty and place it next to the other."""

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
					"place": {"type": "string"},
					"new_topic": {"type": "string"},
					"new_condition": {"type": "string"},
					"repeats": {"type": "string"},
					"replaces": {"type": "string"},
				},
				"required": [
					"question",
					"answer",
					"keywords",
					"confidence",
					"evidence_chat",
					"place",
					"new_topic",
					"new_condition",
					"repeats",
					"replaces",
				],
				"additionalProperties": False,
			},
		}
	},
	"required": ["entries"],
	"additionalProperties": False,
}


def words(text: str) -> set[str]:
	"""The words that carry meaning, a plural counted as its singular (cheques, cheque)."""
	return {
		w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
		for w in re.findall(r"\w+", (text or "").lower())
		if w not in STOP and len(w) > 1
	}


def similarity(a: str, b: str) -> float:
	wa, wb = words(a), words(b)
	return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _similar(question: str, entries: list) -> object | None:
	best = max(entries, key=lambda e: similarity(question, e.question), default=None)
	return best if best and similarity(question, best.question) >= SAME_QUESTION else None


def tree() -> dict:
	"""The topics and conditions of the tree, {name: node}, each with .path (for the AI and emails)."""
	nodes = {
		n.name: n
		for n in frappe.get_all(
			"Periskope Knowledge Entry",
			filters={"node_type": ["in", ["Topic", "Condition"]], "status": ["!=", "Retired"]},
			fields=["name", "node_type", "parent_entry", "question", "keywords", "status"],
			order_by="creation asc",
		)
	}
	for n in nodes.values():
		path, parent, seen = [n.question], n.parent_entry, {n.name}
		while parent in nodes and parent not in seen:
			seen.add(parent)
			path.insert(0, nodes[parent].question)
			parent = nodes[parent].parent_entry
		n.path = " > ".join(path)
	return nodes


def outline(nodes: dict, answers: list | None = None) -> str:
	"""The tree for the AI: topics and conditions by path, and the answers under them."""
	lines = [
		f"[{n.name}] {n.node_type}: {n.path}" + (f" (words: {n.keywords})" if n.keywords else "")
		for n in sorted(nodes.values(), key=lambda n: n.path)
	]
	for e in answers or []:
		where = nodes[e.parent_entry].path if e.parent_entry in nodes else "Customer asks"
		lines.append(f"[{e.name}] Answer under {where}: {e.question} -> {e.answer}")
	return "\n".join(lines) or "(empty)"


def _suggest_node(nodes: dict, node_type: str, name: str, parent: str | None) -> str:
	"""An existing topic or condition of this name under `parent`, or a new Suggested one (approved
	together with the answer under it)."""
	existing = next(
		(
			n.name
			for n in nodes.values()
			if n.node_type == node_type
			and (n.parent_entry or None) == parent
			and n.question.lower() == name.lower()
		),
		None,
	)
	if existing:
		return existing
	node = insert_as_system(
		{
			"doctype": "Periskope Knowledge Entry",
			"node_type": node_type,
			"question": name,
			"parent_entry": parent,
			"status": "Suggested",
			"source": "AI Learned",
		}
	)
	node.path = f"{nodes[parent].path} > {name}" if parent else name
	nodes[node.name] = node
	return node.name


def _place(item: dict, nodes: dict) -> str | None:
	"""Where a mined answer goes: the node the AI named, or a topic of the name it proposed; under a
	new condition when it gave one. None: straight under Customer asks."""
	place = (item.get("place") or "").strip().strip("[]")
	parent = place if place in nodes else None
	topic = (item.get("new_topic") or "").strip()[:60]
	if not parent and topic:
		parent = _suggest_node(nodes, "Topic", topic, None)
	condition = (item.get("new_condition") or "").strip()[:140]
	if parent and condition:
		parent = _suggest_node(nodes, "Condition", condition, parent)
	return parent


def _numbers(text: str) -> set[str]:
	return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def _is_new(nodes: dict, leaf: str, name: str) -> bool:
	"""Whether the node called `name` on the path to `leaf` is one the agent proposed."""
	node = nodes.get(leaf)
	while node:
		if node.question == name:
			return node.status == "Suggested"
		node = nodes.get(node.parent_entry)
	return False


def record(item: dict, evidence: list[str], nodes: dict | None = None) -> str:
	"""Save one mined pair. Returns "added", "seen" (a repeat of an entry) or "changed" or "" (dropped)."""
	question, answer = (item.get("question") or "").strip(), (item.get("answer") or "").strip()
	if not question or not answer or (item.get("confidence") or 0) < MIN_CONFIDENCE:
		return ""
	nodes = tree() if nodes is None else nodes
	entries = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": ["!=", "Retired"], "node_type": "Answer"},
		fields=["name", "question", "answer", "status", "evidence_count", "parent_entry"],
	)
	by_name = {e.name: e for e in entries}
	replaces = by_name.get((item.get("replaces") or "").strip().strip("[]"))
	place = (item.get("place") or "").strip().strip("[]")
	# What the AI pointed at first. Failing that, the same question in the branch it chose, or
	# anywhere when it placed nothing; never when it proposed a new topic or condition, since the same
	# question has one answer per branch (Delivery time: Chennai, Bengaluru, Otherwise).
	new_node = (item.get("new_topic") or "").strip() or (item.get("new_condition") or "").strip()
	if place in nodes:
		pool = [e for e in entries if e.parent_entry == place]
	else:
		pool = [] if new_node else entries
	match = (
		replaces
		or by_name.get((item.get("repeats") or "").strip().strip("[]"))
		or (None if new_node else _similar(question, pool))
		# the same answer under other words is a repeat wherever it was placed
		or next(
			(
				e
				for e in entries
				if similarity(answer, e.answer) >= SAME_ANSWER and _numbers(answer) == _numbers(e.answer)
			),
			None,
		)
	)
	if match:
		frappe.db.set_value(
			"Periskope Knowledge Entry",
			match.name,
			{"evidence_count": (match.evidence_count or 1) + 1, "last_seen": now_datetime()},
			update_modified=False,
		)
		differs = (
			match is replaces
			or similarity(answer, match.answer) < CHANGED_ANSWER
			or _numbers(answer) != _numbers(match.answer)
		)
		if match.status == "Approved" and differs:
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

	parent = _place(item, nodes)
	where = nodes[parent].path if parent else "Customer asks"
	entry = insert_as_system(
		{
			"doctype": "Periskope Knowledge Entry",
			"question": question,
			"answer": answer,
			"keywords": item.get("keywords") or "",
			"parent_entry": parent,
			"status": "Suggested",
			"source": "AI Learned",
			"confidence": item.get("confidence"),
			"evidence_count": 1,
			"last_seen": now_datetime(),
		}
	)
	new = [n for n in where.split(" > ") if parent and _is_new(nodes, parent, n)]
	suggestions.create(
		"Knowledge Entry",
		"Add",
		f"Add to the knowledge base: {question}"[:140],
		reason="A rep gave this answer to a customer; it can answer the same question next time. "
		+ f"Goes under: {where}."
		+ (f" New in the tree: {', '.join(new)}." if new else ""),
		target=entry.name,
		payload={"question": question, "answer": answer, "keywords": item.get("keywords") or ""},
		evidence=evidence,
	)
	return "added"


def mine(transcript: str, chats: dict[int, list[str]]) -> dict:
	"""One AI call over a batch of conversations. `chats` maps the conversation number in the
	transcript to its message names (the evidence)."""
	nodes = tree()
	answers = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": ["!=", "Retired"], "node_type": "Answer"},
		fields=["name", "parent_entry", "question", "answer"],
		order_by="modified desc",
		limit=MAX_OUTLINE_ANSWERS,
	)
	result = assistant.complete_json(
		SYSTEM_PROMPT, f"TREE:\n{outline(nodes, answers)}\n\nCONVERSATIONS:\n{transcript}", SCHEMA
	)
	counts = {"added": 0, "seen": 0, "changed": 0}
	for item in result.get("entries") or []:
		evidence = (chats.get(item.get("evidence_chat")) or [])[:5]
		if not evidence:
			continue  # not from a conversation in this batch
		if not rep_said(item.get("answer"), evidence):
			continue  # the AI's wording, or the customer's: only what a rep really wrote is learned
		outcome = record(item, evidence, nodes)
		if outcome:
			counts[outcome] += 1
	return counts


def rep_said(answer: str | None, evidence: list[str]) -> bool:
	"""Whether a rep's own messages (not the customer's, not the AI's replies) in the conversation of
	`evidence` say this answer: most of its words and every one of its numbers. A mined answer is
	used for auto replies at once, so a customer must not be able to plant one ("Yes, 50% off")."""
	need = words(answer or "")
	if not need:
		return False
	asked = frappe.get_all(
		"Periskope Message",
		filters={"name": ["in", evidence]},
		fields=["chat_id", "message_timestamp"],
	)
	if not asked:
		return False
	since = add_days(
		min(m.message_timestamp for m in asked if m.message_timestamp) or now_datetime(), -REP_LOOKBACK_DAYS
	)
	said = " ".join(
		(m.body or "") + " " + (m.transcript or "")
		for m in frappe.get_all(
			"Periskope Message",
			filters=[
				["chat_id", "in", list({m.chat_id for m in asked})],
				["direction", "=", "Outgoing"],
				["agent_reply", "is", "not set"],
				["is_deleted", "=", 0],
				["message_timestamp", ">=", since],
			],
			fields=["body", "transcript"],
			limit=200,
		)
	)
	return len(need & words(said)) / len(need) >= SAID_BY_REP and _numbers(answer) <= _numbers(said)


def flag_stale() -> int:
	"""Suggest retiring approved entries nobody asked for and no rep repeated in a long time."""
	cutoff = add_days(now_datetime(), -STALE_DAYS)
	rows = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": "Approved", "node_type": "Answer", "creation": ["<", cutoff]},
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


OTHERWISE = {"otherwise", "else", "other", "default", "anything else"}
MAX_BRANCHES = 10


def _trail(entry, nodes: dict) -> list | None:
	"""The topics and conditions above an answer, top first; None when one of them is not approved."""
	trail, seen, parent = [], {entry.name}, entry.parent_entry
	while parent:
		node = nodes.get(parent)
		if not node or node.name in seen:
			return None
		trail.insert(0, node)
		seen.add(node.name)
		parent = node.parent_entry
	return trail


def _describe(entry, trail: list, nodes: dict) -> None:
	"""Set entry.title (the path, for the AI) and entry.text (the answer with when it applies). An
	Otherwise condition names the conditions next to it, so the AI knows what it excludes."""
	entry.title = " > ".join([n.question for n in trail if n.node_type == "Topic"] + [entry.question])
	when = []
	for n in trail:
		if n.node_type != "Condition":
			continue
		if (n.question or "").strip().lower().rstrip(".") not in OTHERWISE:
			when.append(n.question)
			continue
		others = [
			o.question
			for o in nodes.values()
			if o.node_type == "Condition"
			and o.parent_entry == n.parent_entry
			and o.name != n.name
			and (o.question or "").strip().lower().rstrip(".") not in OTHERWISE
		]
		when.append(f"none of these is true: {' / '.join(others)}" if others else "always")
	entry.text = (f"Applies only when: {'; '.join(when)}. " if when else "") + entry.answer


def find(text: str, limit: int = 5) -> list:
	"""Approved answers that match a customer's text, best first (for the auto reply).

	An answer matches by its own question and keywords, or by the topics and conditions above it; one
	word of a topic's name or keywords is enough to bring in the answers under that topic.
	Each topic the customer named brings its best answer and all its condition branches; so does the
	topic of a conditional answer, so the AI sees every branch (Chennai: 2 days, Otherwise:
	5 days) and picks the one whose condition holds. Each has .title and .text."""
	nodes = {
		n.name: n
		for n in frappe.get_all(
			"Periskope Knowledge Entry",
			filters={"status": "Approved"},
			fields=["name", "node_type", "parent_entry", "question", "answer", "keywords"],
		)
	}
	said = words(text)
	topics = {
		n.name
		for n in nodes.values()
		if n.node_type == "Topic" and said & (words(n.question) | words(n.keywords or ""))
	}
	scored, trails = [], {}
	for entry in nodes.values():
		if entry.node_type != "Answer":
			continue
		trail = _trail(entry, nodes)
		if trail is None:
			continue
		trails[entry.name] = trail
		path = " ".join(f"{n.question} {n.keywords or ''}" for n in trail)
		overlap = len(said & (words(entry.question) | words(entry.keywords or "") | words(path)))
		score = max(
			similarity(text, entry.question),
			similarity(text, entry.keywords or "") * 0.8,
			similarity(text, path) * 0.7,
			# a short question: how much of what the customer said the entry covers
			(overlap / len(said)) * 0.3 if said else 0,
		)
		if any(n.name in topics for n in trail):
			score = max(score, 0.15)
		if score >= 0.15 or overlap >= 2:
			# the customer's words in the answer's own question or keywords rank it above answers
			# that only share a topic word
			own = len(said & (words(entry.question) | words(entry.keywords or "")))
			scored.append((score + overlap * 0.01 + own * 0.04, entry))
	ranked = [e for _score, e in sorted(scored, key=lambda s: s[0], reverse=True)]
	found = ranked[:limit]
	# the best answer under each topic the customer named, so one busy topic cannot crowd out another
	for topic in topics:
		best = next((e for e in ranked if any(n.name == topic for n in trails[e.name])), None)
		if best and best not in found:
			found.append(best)
	# the condition branches of every named topic and next to every conditional answer, so the AI
	# can pick the branch that holds
	branching = set(topics)
	for entry in found:
		if any(n.node_type == "Condition" for n in trails[entry.name]):
			branching.add(
				next((n.name for n in reversed(trails[entry.name]) if n.node_type == "Topic"), None)
			)
	branching.discard(None)
	for name, other in trails.items():
		if len(found) >= MAX_BRANCHES:
			break
		if (
			any(n.name in branching for n in other)
			and any(n.node_type == "Condition" for n in other)
			and nodes[name] not in found
		):
			found.append(nodes[name])
	for entry in found:
		_describe(entry, trails[entry.name], nodes)
	return found
