"""Suggested changes: the only way the learning agent can change how the app is set up.

The agent writes a Periskope Suggestion and the managers get an email with Approve / Reject links.
A change to the configuration is applied here, and only here, after somebody approved that one
suggestion. Knowledge base changes are applied at once (AUTOMATIC_KINDS). Every suggestion stays as a
row with its outcome, so the list is also the audit log.

The link in the email opens a small page that asks for one confirming click (the page is
www/periskope-suggestion): mail scanners open links on their own, so a plain GET must never decide.
"""

import hashlib
import json
import re
import secrets
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, escape_html, get_datetime, now_datetime

from periskope_connector import query, safety
from periskope_connector.api.permission import require_manager
from periskope_connector.system import insert_as_system, save_as_system

KINDS = ("Tag", "Setting", "Knowledge Entry", "Group Mapping", "Ignore Contact")
OPERATIONS = ("Add", "Modify", "Remove")
# Applied as soon as the agent makes them: the team keeps the knowledge base by correcting it on the
# canvas, not by approving each answer. Everything else changes the configuration and waits.
AUTOMATIC_KINDS = ("Knowledge Entry",)
AUTOMATIC = "Automatic"
REJECTED_QUIET_DAYS = 30
MAX_PER_EMAIL = 25
EXPIRY_DAYS = 7  # a suggestion nobody decided on is dropped after this
EMAIL_GAP_HOURS = 6  # at most one suggestions email in this time

# The settings the agent may suggest changing. Keys, webhook, e-mail lists and switches are left
# out on purpose: those are the team's decision, not something to learn from chats.
LEARNABLE_SETTINGS = (
	"working_days",
	"working_hours_start",
	"working_hours_end",
	"ai_business_context",
	"reply_delay_minutes",
)
PAYLOAD_FIELDS = {
	"Tag": ("tag_name", "description", "active"),
	"Setting": ("value",),
	"Knowledge Entry": ("question", "answer", "keywords"),
	# target: the group's chat id. A group about one customer is linked to its company, deal or lead.
	"Group Mapping": ("group_type", "organization", "crm_deal", "crm_lead"),
	# target: the contact's number (the unmatched-contact queue).
	"Ignore Contact": ("status",),
}


def _signature(kind, operation, target, payload) -> str:
	text = json.dumps([kind, operation, target or "", payload or {}], sort_keys=True, default=str)
	return hashlib.sha1(text.encode()).hexdigest()


def _hash(token: str) -> str:
	return hashlib.sha256(token.encode()).hexdigest()


def clean_payload(kind: str, payload: dict | None) -> dict:
	allowed = PAYLOAD_FIELDS[kind]
	return {k: v for k, v in (payload or {}).items() if k in allowed}


NAME_FIELDS = {"Tag": "tag_name"}


def _already_pending(kind, operation, target, payload) -> bool:
	"""One open question per record: a change to something that already has a pending suggestion,
	or a new tag of a name already proposed, waits for that decision instead."""
	pending = frappe.get_all(
		"Periskope Suggestion",
		filters={"status": "Pending", "kind": kind},
		fields=["operation", "target", "payload"],
	)
	if target and operation != "Add":
		return any(p.target == target and p.operation != "Add" for p in pending)
	field = NAME_FIELDS.get(kind)
	name = _plain(payload.get(field)) if field else ""
	if not name:
		return False
	for p in pending:
		try:
			other = json.loads(p.payload or "{}")
		except ValueError:
			continue
		if _plain(other.get(field)) == name:
			return True
	return False


def _plain(name) -> str:
	"""A name to compare: "Payment Methods" and "payment_method" are the same tag."""
	return re.sub(r"[^a-z0-9]", "", str(name or "").lower()).removesuffix("s")


def create(kind, operation, summary, reason="", target="", payload=None, current=None, evidence=None):
	"""Save a suggestion: pending, or applied at once for AUTOMATIC_KINDS. Returns the document, or None
	when the same suggestion is already waiting, was applied lately, or was rejected in the last 30
	days (a "no" is remembered)."""
	if kind not in KINDS or operation not in OPERATIONS:
		return None
	payload = clean_payload(kind, payload)
	if kind == "Setting" and (target not in LEARNABLE_SETTINGS or "value" not in payload):
		return None
	if _already_pending(kind, operation, target, payload):
		return None
	signature = _signature(kind, operation, target, payload)
	since = add_to_date(now_datetime(), days=-REJECTED_QUIET_DAYS)
	if frappe.db.exists(
		"Periskope Suggestion",
		{
			"signature": signature,
			"status": ["in", ["Pending", "Rejected", "Applied"]],
			"modified": [">=", since],
		},
	):
		return None
	doc = insert_as_system(
		{
			"doctype": "Periskope Suggestion",
			"kind": kind,
			"operation": operation,
			"target": target or "",
			"summary": (summary or "")[:140],
			"reason": reason,
			"payload": json.dumps(payload, indent=1, default=str),
			"current_value": json.dumps(current or {}, indent=1, default=str),
			"evidence_messages": "\n".join(evidence or []),
			"status": "Pending",
			"signature": signature,
			"expires_on": add_to_date(now_datetime(), days=EXPIRY_DAYS),
		}
	)
	if kind in AUTOMATIC_KINDS:
		approve(doc, AUTOMATIC, "Administrator")
	return doc


# --- Applying -----------------------------------------------------------------------------------


def _payload(doc) -> dict:
	try:
		return clean_payload(doc.kind, json.loads(doc.payload or "{}"))
	except ValueError:
		frappe.throw(_("The suggestion has no readable change"))


def _settings_doc():
	settings = frappe.get_doc("Periskope Settings")
	settings.flags.ignore_permissions = True
	return settings


def _apply_tag(doc, data: dict):
	if doc.operation == "Add":
		name = (data.get("tag_name") or "").strip()
		if frappe.db.exists("Periskope Tag", name):
			frappe.throw(_("Tag {0} already exists").format(name))
		frappe.get_doc(
			{
				"doctype": "Periskope Tag",
				"tag_name": name,
				"description": data.get("description") or "",
				"active": 1,
				"sort_order": 500,
			}
		).insert(ignore_permissions=True)
		return
	tag = frappe.get_doc("Periskope Tag", doc.target)
	if doc.operation == "Remove":
		tag.active = 0
	else:
		if "description" in data:
			tag.description = data["description"]
		if "active" in data:
			tag.active = 1 if data["active"] else 0
	save_as_system(tag)


def _apply_setting(doc, data: dict):
	if doc.target not in LEARNABLE_SETTINGS:
		frappe.throw(_("{0} cannot be changed by the agent").format(doc.target))
	settings = _settings_doc()
	settings.set(doc.target, data["value"])
	settings.save()


def _apply_knowledge(doc, data: dict):
	entry = frappe.get_doc("Periskope Knowledge Entry", doc.target)
	if doc.operation == "Remove":
		entry.status = "Retired"
	else:
		for field in ("question", "answer", "keywords"):
			if data.get(field):
				entry.set(field, data[field])
		entry.status = "Approved"
		# a topic the agent proposed for this answer is approved with it
		parent = entry.parent_entry
		while parent:
			node = frappe.db.get_value(
				"Periskope Knowledge Entry", parent, ["status", "parent_entry"], as_dict=True
			)
			if not node:
				break
			if node.status == "Suggested":
				frappe.db.set_value("Periskope Knowledge Entry", parent, "status", "Approved")
			parent = node.parent_entry
	save_as_system(entry)


def _apply_group_mapping(doc, data: dict):
	from periskope_connector.linking import link_message

	group = frappe.db.get_value("Periskope Group", {"chat_id": doc.target})
	if not group:
		frappe.throw(_("Group {0} no longer exists").format(doc.target))
	values = {}
	if data.get("group_type") in ("Customer", "Internal"):
		values["group_type"] = data["group_type"]
	if data.get("organization"):
		organization = frappe.db.get_value("CRM Organization", {"organization_name": data["organization"]})
		values["organization"] = (
			organization
			or insert_as_system(
				{"doctype": "CRM Organization", "organization_name": data["organization"]}
			).name
		)
	for field, doctype in (("crm_deal", "CRM Deal"), ("crm_lead", "CRM Lead")):
		if data.get(field):
			if not frappe.db.exists(doctype, data[field]):
				frappe.throw(_("{0} {1} no longer exists").format(doctype, data[field]))
			values[field] = data[field]
	if not values:
		frappe.throw(_("Nothing to change"))
	frappe.db.set_value("Periskope Group", group, values)
	# The group's messages follow the new mapping.
	for name in frappe.get_all(
		"Periskope Message",
		filters={"chat_id": doc.target},
		pluck="name",
		order_by="creation desc",
		limit=500,
	):
		link_message(name)


def _apply_ignore_contact(doc, data: dict):
	from periskope_connector.contacts import IGNORED_STATUSES

	status = data.get("status") if data.get("status") in IGNORED_STATUSES else "Ignored (Personal)"
	name = frappe.db.get_value("Periskope Contact", {"phone": doc.target})
	if doc.operation == "Remove":
		if name:
			frappe.db.set_value("Periskope Contact", name, "status", "New")
		return
	if name:
		frappe.db.set_value("Periskope Contact", name, "status", status)
	else:
		insert_as_system({"doctype": "Periskope Contact", "phone": doc.target, "status": status})


APPLIERS = {
	"Tag": _apply_tag,
	"Setting": _apply_setting,
	"Knowledge Entry": _apply_knowledge,
	"Group Mapping": _apply_group_mapping,
	"Ignore Contact": _apply_ignore_contact,
}


def _finish(doc, status: str, via: str, user: str, error: str = ""):
	doc.db_set(
		{
			"status": status,
			"decided_by": user,
			"decided_on": now_datetime(),
			"decision_via": via,
			"applied_error": error[:500],
		}
	)


def approve(doc, via: str, user: str) -> str:
	"""Apply the change and record the outcome. Returns Applied or Failed; a change that cannot be
	applied (the record is gone, a value is not valid) is kept as Failed with the reason."""
	try:
		frappe.db.savepoint("periskope_suggestion")
		APPLIERS[doc.kind](doc, _payload(doc))
	except Exception as e:
		frappe.db.rollback(save_point="periskope_suggestion")
		_finish(doc, "Failed", via, user, str(e))
		return "Failed"
	_finish(doc, "Applied", via, user)
	return "Applied"


def reject(doc, via: str, user: str) -> str:
	_finish(doc, "Rejected", via, user)
	return "Rejected"


def undo(doc, via: str, user: str) -> str:
	"""Take back a knowledge change the agent applied by itself: a new answer is retired, a changed
	answer gets its old text back, a retired one comes back."""
	if doc.kind not in AUTOMATIC_KINDS or doc.status != "Applied":
		frappe.throw(_("Only a change the assistant applied by itself can be undone."))
	entry = frappe.get_doc("Periskope Knowledge Entry", doc.target)
	if doc.operation == "Add":
		entry.status = "Retired"
	elif doc.operation == "Modify":
		before = frappe.parse_json(doc.current_value or "{}") or {}
		for field in ("question", "answer", "keywords"):
			if before.get(field):
				entry.set(field, before[field])
	else:  # Remove
		entry.status = "Approved"
	save_as_system(entry)
	_finish(doc, "Undone", via, user)
	return "Undone"


def decide_by_token(token: str, decision: str) -> dict:
	"""The email link: one suggestion, one use. Returns what happened for the confirmation page."""
	token = (token or "").strip()
	name = frappe.db.get_value("Periskope Suggestion", {"token_hash": _hash(token)}) if token else None
	if not name:
		return {"ok": False, "message": _("This link is not valid.")}
	doc = frappe.get_doc("Periskope Suggestion", name)
	if decision == "undo":
		if doc.status != "Applied" or doc.kind not in AUTOMATIC_KINDS:
			return {"ok": False, "message": _("This change was already {0}.").format(doc.status.lower())}
		if doc.expires_on and get_datetime(doc.expires_on) < now_datetime():
			return {"ok": False, "message": _("This link has expired.")}
		return {"ok": True, "outcome": undo(doc, "Email Link", _("Email link")), "message": "Undone"}
	if doc.status != "Pending":
		return {"ok": False, "message": _("This suggestion was already {0}.").format(doc.status.lower())}
	if doc.expires_on and get_datetime(doc.expires_on) < now_datetime():
		doc.db_set("status", "Expired")
		return {"ok": False, "message": _("This suggestion has expired.")}
	user = _("Email link")
	outcome = (approve if decision == "approve" else reject)(doc, "Email Link", user)
	return {"ok": outcome != "Failed", "outcome": outcome, "message": outcome, "error": doc.applied_error}


def describe_token(token: str) -> dict | None:
	"""What the confirmation page shows for a link, without deciding anything."""
	name = frappe.db.get_value("Periskope Suggestion", {"token_hash": _hash(token or "")})
	if not name:
		return None
	doc = frappe.db.get_value(
		"Periskope Suggestion",
		name,
		[
			"kind",
			"operation",
			"target",
			"summary",
			"reason",
			"payload",
			"current_value",
			"status",
			"expires_on",
		],
		as_dict=True,
	)
	return doc


# --- The email ------------------------------------------------------------------------------------


def _recipients() -> list[str]:
	from periskope_connector.reminders import get_config

	raw = frappe.db.get_single_value("Periskope Settings", "approver_emails") or ""
	listed = [e.strip() for e in re.split(r"[,\n;]", raw) if e.strip()]
	return listed or get_config().emails


def expire_old() -> int:
	rows = frappe.get_all(
		"Periskope Suggestion",
		filters={"status": "Pending", "expires_on": ["<", now_datetime()]},
		pluck="name",
	)
	for name in rows:
		frappe.db.set_value("Periskope Suggestion", name, "status", "Expired")
	return len(rows)


def send_digest(force: bool = False) -> int:
	"""Email the pending suggestions that were not mailed yet, all in one message. Returns how many
	were included. Does nothing when nobody is set to receive them or when the last email was
	sent less than the configured gap ago (unless forced)."""
	from periskope_connector.webhooks import site_url

	expire_old()
	rows = frappe.get_all(
		"Periskope Suggestion",
		filters={"status": "Pending", "emailed_on": ["is", "not set"]},
		fields=["name", "kind", "operation", "target", "summary", "reason", "payload", "current_value"],
		order_by="creation asc",
		limit=MAX_PER_EMAIL,
	)
	if not rows:
		return 0
	last = query.latest("Periskope Suggestion", "emailed_on")
	if not force and last and get_datetime(last) > now_datetime() - timedelta(hours=EMAIL_GAP_HOURS):
		return 0
	recipients = _recipients()
	if not recipients:
		return 0

	batch = secrets.token_hex(6)
	base = f"{site_url()}/periskope-suggestion"
	blocks = []
	for row in rows:
		token = secrets.token_urlsafe(32)
		frappe.db.set_value(
			"Periskope Suggestion",
			row.name,
			{"token_hash": _hash(token), "batch": batch, "emailed_on": now_datetime()},
			update_modified=False,
		)
		approve_link = f"{base}?token={token}&decision=approve"
		reject_link = f"{base}?token={token}&decision=reject"
		blocks.append(
			f"<div style='border:1px solid #ddd;border-radius:6px;padding:12px;margin:12px 0'>"
			f"<p><b>{escape_html(row.kind)} &middot; {escape_html(row.operation)}</b>"
			f"{' &middot; ' + escape_html(row.target) if row.target else ''}</p>"
			f"<p>{escape_html(row.summary)}</p>"
			f"<p style='color:#555'>{_('Why')}: {escape_html(row.reason or '')}</p>"
			f"<pre style='background:#f6f6f6;padding:8px;white-space:pre-wrap'>{escape_html(row.payload or '')}</pre>"
			f"<p><a href='{approve_link}' style='background:#2e7d32;color:#fff;padding:8px 14px;"
			f"border-radius:4px;text-decoration:none'>{_('Approve')}</a> &nbsp; "
			f"<a href='{reject_link}' style='background:#c62828;color:#fff;padding:8px 14px;"
			f"border-radius:4px;text-decoration:none'>{_('Reject')}</a></p></div>"
		)
	safety.sendmail(
		recipients=recipients,
		subject=_("Periskope assistant: {0} suggested change(s) to approve").format(len(rows)),
		message=(
			f"<p>{_('The assistant learned from recent conversations and suggests these changes. Nothing changes until you approve it.')}</p>"
			+ "".join(blocks)
			+ f"<p style='color:#777'>{_('Each link works once and expires on its own.')}</p>"
		),
		reference_doctype="Periskope Suggestion",
		reference_name=rows[0].name,
		now=False,
	)
	return len(rows)


def send_applied_digest() -> int:
	"""Scheduler, daily: email the approvers the knowledge changes the assistant made by itself since
	the last email, each with an Undo link (valid until the change's expiry). Returns how many."""
	from periskope_connector.webhooks import site_url

	rows = frappe.get_all(
		"Periskope Suggestion",
		filters={
			"kind": ["in", list(AUTOMATIC_KINDS)],
			"status": "Applied",
			"decision_via": AUTOMATIC,
			"emailed_on": ["is", "not set"],
			"expires_on": [">", now_datetime()],
		},
		fields=["name", "operation", "summary", "payload", "current_value"],
		order_by="creation asc",
		limit=MAX_PER_EMAIL * 2,
	)
	recipients = _recipients()
	if not rows or not recipients:
		return 0
	base = f"{site_url()}/periskope-suggestion"
	blocks = []
	for row in rows:
		token = secrets.token_urlsafe(32)
		frappe.db.set_value(
			"Periskope Suggestion",
			row.name,
			{"token_hash": _hash(token), "emailed_on": now_datetime()},
			update_modified=False,
		)
		answer = (frappe.parse_json(row.payload or "{}") or {}).get("answer") or ""
		before = (frappe.parse_json(row.current_value or "{}") or {}).get("answer") or ""
		blocks.append(
			f"<div style='border:1px solid #ddd;border-radius:6px;padding:12px;margin:12px 0'>"
			f"<p><b>{escape_html(row.summary)}</b></p>"
			+ (f"<p style='color:#777'>{_('Before')}: {escape_html(before)}</p>" if before else "")
			+ (f"<p>{_('Now')}: {escape_html(answer)}</p>" if answer else "")
			+ f"<p><a href='{base}?token={token}&decision=undo' style='background:#c62828;color:#fff;"
			f"padding:6px 12px;border-radius:4px;text-decoration:none'>{_('Undo')}</a></p></div>"
		)
	safety.sendmail(
		recipients=recipients,
		subject=_("Periskope assistant: {0} knowledge change(s) applied").format(len(rows)),
		message=(
			f"<p>{_('The assistant learned these answers from what your reps told customers, and uses them in its replies from now on. Undo any that is wrong.')}</p>"
			+ "".join(blocks)
		),
		reference_doctype="Periskope Suggestion",
		reference_name=rows[0].name,
		now=False,
	)
	return len(rows)


# --- Desk buttons -----------------------------------------------------------------------------------


@frappe.whitelist()
def decide(name: str, decision: str) -> str:
	"""Approve or reject from the desk (managers only)."""
	require_manager()
	doc = frappe.get_doc("Periskope Suggestion", name)
	if doc.status != "Pending":
		frappe.throw(_("This suggestion is already {0}").format(doc.status.lower()))
	if decision not in ("approve", "reject"):
		frappe.throw(_("Unknown decision"))
	return (approve if decision == "approve" else reject)(doc, "Desk", frappe.session.user)
