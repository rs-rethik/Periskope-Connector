"""The Knowledge Base canvas (desk page "periskope-knowledge"): the knowledge base as a node graph.

Admins draw the tree the auto reply answers from: Topic nodes (Delivery) branch into Condition nodes
(Customer is in Chennai, Otherwise) and end in Answer nodes; see knowledge.find for how a customer's
message is matched against it. Every node is a Periskope Knowledge Entry, and its parent_entry is the
wire into it. The canvas saves as the admin edits; moving a node saves only its position.

A document (price list, FAQ, catalogue) can be imported: the AI extracts answers from it, the admin
sees a preview and picks what to keep, and only confirm_import() writes. The gaps panel lists what
customers asked that the agent could not answer. Admins only (System Manager, WhatsApp Manager).
"""

import io
import json
import secrets

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

from periskope_connector import knowledge, safety
from periskope_connector.ai import assistant
from periskope_connector.api.permission import require_admin

DOCTYPE = "Periskope Knowledge Entry"
FIELDS = ["question", "answer", "keywords", "node_type", "parent_entry"]
MAX_DOC_CHARS = 15000
DRAFT_MINUTES = 30
MAX_IMPORT = 40

IMPORT_PROMPT = """You build a knowledge base for a company's WhatsApp customer chats from a document the company uploaded (a price list, FAQ, catalogue or policy).

Extract question and answer pairs a customer could ask and the document clearly answers: product facts, prices, availability, minimum order, lead time, delivery or payment terms, how a process works, company details. Keep every number exactly as the document gives it. Write the question and answer in plain, general wording; one fact per pair. Leave out anything the document is unsure about and any personal data. If nothing qualifies, return an empty list."""

IMPORT_SCHEMA = {
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
				},
				"required": ["question", "answer", "keywords"],
				"additionalProperties": False,
			},
		}
	},
	"required": ["entries"],
	"additionalProperties": False,
}


def _log(operation: str, doc) -> None:
	safety.log(
		"Knowledge Change",
		safety.LIVE,
		f"{operation} {doc.node_type}: {(doc.question or '')[:100]}",
		origin="Knowledge Canvas",
		target_doctype=DOCTYPE,
		target_name=doc.name,
		detail={"user": frappe.session.user},
	)


def _node(doc) -> dict:
	placed = bool(doc.pos_x or doc.pos_y)  # (0, 0) is where the canvas has not put it yet
	return {
		"name": doc.name,
		"node_type": doc.node_type or "Answer",
		"parent": doc.parent_entry or "",
		"question": doc.question,
		"answer": doc.answer or "",
		"keywords": doc.keywords or "",
		"status": doc.status,
		"source": doc.source,
		"x": doc.pos_x if placed else None,
		"y": doc.pos_y if placed else None,
		"last_used": str(doc.last_used or ""),
	}


@frappe.whitelist()
def get_graph() -> dict:
	"""{nodes, suggestions, gaps}: every node that is not retired, the pending suggestion of each
	node the agent learned, and how many questions are waiting for an answer."""
	require_admin()
	rows = frappe.get_all(
		DOCTYPE,
		filters={"status": ["!=", "Retired"]},
		fields=["name", "status", "source", "pos_x", "pos_y", "last_used", *FIELDS],
		order_by="creation asc",
	)
	pending = frappe.get_all(
		"Periskope Suggestion",
		filters={"status": "Pending", "kind": "Knowledge Entry", "target": ["in", [r.name for r in rows]]},
		fields=["name", "target"],
	)
	return {
		"nodes": [_node(r) for r in rows],
		"suggestions": {s.target: s.name for s in pending},
		"gaps": len(gaps()),
	}


@frappe.whitelist()
def save_node(node: str | dict) -> dict:
	"""Create a node (no name) or change one. An admin's new node is Approved at once."""
	require_admin()
	node = json.loads(node) if isinstance(node, str) else node
	values = {k: (node.get(k) or "").strip() if k != "parent_entry" else node.get(k) or None for k in FIELDS}
	if node.get("name"):
		doc = frappe.get_doc(DOCTYPE, node["name"])
		doc.update(values)
		doc.save()
		_log("Change", doc)
	else:
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				**values,
				"status": "Approved",
				"source": "Manual",
				"confidence": 100,
				"pos_x": node.get("x"),
				"pos_y": node.get("y"),
			}
		).insert()
		_log("Add", doc)
	return _node(doc)


@frappe.whitelist()
def connect(child: str, parent: str | None = None) -> dict:
	"""Wire `child` under `parent` (empty: under Customer asks)."""
	require_admin()
	doc = frappe.get_doc(DOCTYPE, child)
	doc.parent_entry = parent or None
	doc.save()
	_log("Connect", doc)
	return _node(doc)


@frappe.whitelist()
def move_nodes(positions: str | list) -> None:
	"""Save where nodes sit on the canvas: [{name, x, y}]."""
	require_admin()
	for p in json.loads(positions) if isinstance(positions, str) else positions:
		if frappe.db.exists(DOCTYPE, p.get("name")):
			frappe.db.set_value(
				DOCTYPE,
				p["name"],
				{"pos_x": int(p.get("x") or 0), "pos_y": int(p.get("y") or 0)},
				update_modified=False,
			)


@frappe.whitelist()
def remove_node(name: str) -> dict:
	"""Retire a node and everything under it; the auto reply stops using them. Returns how many."""
	require_admin()
	todo, removed = [name], 0
	while todo:
		doc = frappe.get_doc(DOCTYPE, todo.pop())
		todo += frappe.get_all(
			DOCTYPE, filters={"parent_entry": doc.name, "status": ["!=", "Retired"]}, pluck="name"
		)
		doc.status = "Retired"
		doc.save()
		_log("Retire", doc)
		removed += 1
	return {"removed": removed}


@frappe.whitelist()
def gaps(days: int = 14) -> list[dict]:
	"""What customers asked that the agent answered with the holding message: [{question, when}]."""
	require_admin()
	rows = frappe.get_all(
		"Periskope Auto Reply",
		filters={
			"status": ["in", ["Holding Sent", "Dry Run"]],
			"modified": [">=", add_days(now_datetime(), -int(days or 14))],
			"customer_text": ["is", "set"],
		},
		fields=["customer_text", "modified", "status", "cancel_reason"],
		order_by="modified desc",
		limit=60,
	)
	seen, found = set(), []
	for row in rows:
		if row.status == "Dry Run" and row.cancel_reason != "Holding message":
			continue  # a dry run that had an answer
		text = (row.customer_text or "").strip()[:300]
		key = " ".join(sorted(knowledge.words(text)))[:120]
		if text and key not in seen:
			seen.add(key)
			found.append({"question": text, "when": str(row.modified)})
	return found[:15]


# --- Documents ----------------------------------------------------------------------------------


def document_text(file_url: str) -> str:
	"""The text of an uploaded PDF, spreadsheet, Word file, CSV or text file (cut at MAX_DOC_CHARS)."""
	file = frappe.get_doc("File", {"file_url": file_url})
	content = file.get_content()
	name = (file.file_name or "").lower()
	if isinstance(content, str):
		content = content.encode()
	if name.endswith(".pdf"):
		from pypdf import PdfReader

		text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
	elif name.endswith((".xlsx", ".xlsm")):
		from openpyxl import load_workbook

		book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		lines = []
		for sheet in book.worksheets:
			for row in sheet.iter_rows(values_only=True):
				cells = [str(c) for c in row if c not in (None, "")]
				if cells:
					lines.append(" | ".join(cells))
		text = "\n".join(lines)
	elif name.endswith(".docx"):
		from docx import Document

		text = "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
	else:
		text = content.decode("utf-8", errors="replace")
	return text[:MAX_DOC_CHARS]


@frappe.whitelist()
def import_document(file_url: str) -> dict:
	"""Read a document and propose answers from it: {entries, token}, or {error}. Nothing is saved."""
	require_admin()
	ready, reason = assistant.readiness()
	if not ready:
		return {
			"error": _("The AI cannot be used right now: {0}").format(
				_("the monthly budget is used up") if reason == "budget" else _("no AI key is set")
			)
		}
	text = document_text(file_url)
	if not text.strip():
		return {"error": _("No text could be read from this document.")}
	result = assistant.complete_json(IMPORT_PROMPT, f"DOCUMENT:\n{text}", IMPORT_SCHEMA, 4000)
	entries = [
		{k: (e.get(k) or "").strip() for k in ("question", "answer", "keywords")}
		for e in result.get("entries") or []
		if (e.get("question") or "").strip() and (e.get("answer") or "").strip()
	][:MAX_IMPORT]
	if not entries:
		return {"error": _("The document has nothing customers could ask about.")}
	token = secrets.token_urlsafe(16)
	frappe.cache().set_value(
		f"periskope_kb_import:{token}",
		{"user": frappe.session.user, "entries": entries},
		expires_in_sec=DRAFT_MINUTES * 60,
	)
	return {"entries": entries, "token": token}


@frappe.whitelist()
def confirm_import(token: str, picks: str | list, parent: str | None = None) -> dict:
	"""Save the previewed answers the admin kept (`picks`: their positions in the preview) under
	`parent`. Returns {saved: [node]}."""
	require_admin()
	key = f"periskope_kb_import:{token}"
	draft = frappe.cache().get_value(key)
	if not draft or draft.get("user") != frappe.session.user:
		frappe.throw(_("This preview has expired. Import the document again."))
	frappe.cache().delete_value(key)
	picks = json.loads(picks) if isinstance(picks, str) else picks
	saved = []
	for i in picks:
		if isinstance(i, int) and 0 <= i < len(draft["entries"]):
			entry = draft["entries"][i]
			saved.append(save_node({**entry, "node_type": "Answer", "parent_entry": parent}))
	return {"saved": saved}
