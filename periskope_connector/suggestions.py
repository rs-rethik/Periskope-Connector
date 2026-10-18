"""Suggested changes: the only way the learning agent can change how the app is set up.

The agent writes a Periskope Suggestion and the managers get an email with Approve / Reject links.
A change is applied here, and only here, after somebody approved that one suggestion. Every
suggestion stays as a row with its outcome, so the list is also the audit log.

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

from periskope_connector import query
from periskope_connector.api.permission import require_manager
from periskope_connector.system import insert_as_system, save_as_system

KINDS = (
	"Tag",
	"Keyword Rule",
	"SLA Level",
	"Setting",
	"Knowledge Entry",
	"Group Mapping",
	"Ignore Contact",
	"Lead Rule",
)
OPERATIONS = ("Add", "Modify", "Remove")
REJECTED_QUIET_DAYS = 30
MAX_PER_EMAIL = 25

# The settings the agent may suggest changing. Keys, webhook, e-mail lists and switches are left
# out on purpose: those are the team's decision, not something to learn from chats.
LEARNABLE_SETTINGS = (
	"working_days",
	"working_hours_start",
	"working_hours_end",
	"ack_words",
	"keyword_dedupe_hours",
	"burst_minutes",
	"context_messages",
	"ai_business_context",
	"reply_delay_minutes",
	"reply_delay_jitter_percent",
	"reply_min_confidence",
	"holding_message",
)
# No recipients: who gets emailed is the team's decision too (a new rule mails the alert list).
PAYLOAD_FIELDS = {
	"Tag": ("tag_name", "description", "active"),
	"Keyword Rule": ("rule_name", "keywords", "enabled"),
	"SLA Level": ("wait_minutes", "min_severity", "action"),
	"Setting": ("value",),
	"Knowledge Entry": ("question", "answer", "keywords"),
	# target: the group's chat id. A group about one customer is linked to its company, deal or lead.
	"Group Mapping": ("group_type", "organization", "crm_deal", "crm_lead"),
	# target: the contact's number (the unmatched-contact queue).
	"Ignore Contact": ("status",),
	# target: a tag. Whether its messages from unknown numbers create a lead.
	"Lead Rule": ("create_lead",),
}


def _signature(kind, operation, target, payload) -> str:
	text = json.dumps([kind, operation, target or "", payload or {}], sort_keys=True, default=str)
	return hashlib.sha1(text.encode()).hexdigest()


def _hash(token: str) -> str:
	return hashlib.sha256(token.encode()).hexdigest()


def clean_payload(kind: str, payload: dict | None) -> dict:
	allowed = PAYLOAD_FIELDS[kind]
	return {k: v for k, v in (payload or {}).items() if k in allowed}


NAME_FIELDS = {"Tag": "tag_name", "Keyword Rule": "rule_name"}
SAME_RULE_WORDS = 0.6  # share of the smaller rule's words from which two keyword rules are the same


def _already_pending(kind, operation, target, payload) -> bool:
	"""One open question per record: a change to something that already has a pending suggestion,
	or a new tag or rule of a name already proposed, waits for that decision instead."""
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
	words = _words(payload.get("keywords")) if kind == "Keyword Rule" else set()
	for p in pending:
		try:
			other = json.loads(p.payload or "{}")
		except ValueError:
			continue
		if _plain(other.get(field)) == name:
			return True
		# the same alert under another name: most of its words are already proposed
		theirs = _words(other.get("keywords"))
		if words and theirs and len(words & theirs) / min(len(words), len(theirs)) >= SAME_RULE_WORDS:
			return True
	return False


def _words(text) -> set[str]:
	return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _plain(name) -> str:
	"""A name to compare: "Payment Methods" and "payment_method" are the same rule."""
	return re.sub(r"[^a-z0-9]", "", str(name or "").lower()).removesuffix("s")


def create(kind, operation, summary, reason="", target="", payload=None, current=None, evidence=None):
	"""Save a pending suggestion. Returns the document, or None when the same suggestion is already
	waiting, was applied lately, or was rejected in the last 30 days (a "no" is remembered)."""
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
	settings = frappe.db.get_single_value("Periskope Settings", "suggestion_expiry_days") or 7
	return insert_as_system(
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
			"expires_on": add_to_date(now_datetime(), days=int(settings)),
		}
	)


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
	from periskope_connector.tags import default_actions

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
				"actions": default_actions(name),
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


def _apply_keyword_rule(doc, data: dict):
	if doc.operation == "Add":
		insert_as_system(
			{
				"doctype": "Periskope Keyword Rule",
				"rule_name": data.get("rule_name"),
				"keywords": data.get("keywords"),
				"recipients": _settings_doc().alert_emails,
				"enabled": 1,
			}
		)
		return
	name = frappe.db.get_value("Periskope Keyword Rule", {"rule_name": doc.target})
	if not name:
		frappe.throw(_("{0} no longer exists").format(doc.target))
	rule = frappe.get_doc("Periskope Keyword Rule", name)
	if doc.operation == "Remove":
		rule.enabled = 0
	else:
		for field in ("keywords", "enabled"):
			if field in data:
				rule.set(field, data[field])
	save_as_system(rule)


def _apply_sla_level(doc, data: dict):
	if doc.operation == "Add":
		insert_as_system(
			{"doctype": "Periskope SLA Level", **{k: v for k, v in data.items() if v not in (None, "")}}
		)
		return
	if not frappe.db.exists("Periskope SLA Level", doc.target):
		frappe.throw(_("That SLA level no longer exists"))
	if doc.operation == "Remove":
		frappe.delete_doc("Periskope SLA Level", doc.target, ignore_permissions=True)
		return
	level = frappe.get_doc("Periskope SLA Level", doc.target)
	for field, value in data.items():
		level.set(field, value)
	save_as_system(level)


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


def _apply_lead_rule(doc, data: dict):
	from periskope_connector.actions import CREATE_LEAD
	from periskope_connector.flow import TAG_ACTIONS

	if not frappe.db.exists("Periskope Tag", doc.target):
		frappe.throw(_("Tag {0} no longer exists").format(doc.target))
	tag = frappe.get_doc("Periskope Tag", doc.target)
	want = doc.operation != "Remove" and bool(data.get("create_lead", True))
	rows = [r for r in tag.actions if r.action == CREATE_LEAD]
	if want and not rows:
		tag.append("actions", {"action": CREATE_LEAD, **dict(TAG_ACTIONS)[CREATE_LEAD]})
	elif not want and rows:
		tag.set("actions", [r for r in tag.actions if r.action != CREATE_LEAD])
	else:
		return
	save_as_system(tag)


APPLIERS = {
	"Tag": _apply_tag,
	"Keyword Rule": _apply_keyword_rule,
	"SLA Level": _apply_sla_level,
	"Setting": _apply_setting,
	"Knowledge Entry": _apply_knowledge,
	"Group Mapping": _apply_group_mapping,
	"Ignore Contact": _apply_ignore_contact,
	"Lead Rule": _apply_lead_rule,
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
	if doc.kind == "Knowledge Entry" and doc.operation == "Add" and doc.target:
		entry = frappe.db.get_value(
			"Periskope Knowledge Entry", doc.target, ["status", "parent_entry"], as_dict=True
		)
		if entry and entry.status == "Suggested":
			frappe.db.set_value("Periskope Knowledge Entry", doc.target, "status", "Retired")
			# a topic or condition the agent proposed goes too once nothing is left under it
			parent = entry.parent_entry
			while (
				parent
				and frappe.db.get_value("Periskope Knowledge Entry", parent, "status") == "Suggested"
				and not frappe.db.exists(
					"Periskope Knowledge Entry", {"parent_entry": parent, "status": ["!=", "Retired"]}
				)
			):
				frappe.db.set_value("Periskope Knowledge Entry", parent, "status", "Retired")
				parent = frappe.db.get_value("Periskope Knowledge Entry", parent, "parent_entry")
	return "Rejected"


def decide_by_token(token: str, decision: str) -> dict:
	"""The email link: one suggestion, one use. Returns what happened for the confirmation page."""
	token = (token or "").strip()
	name = frappe.db.get_value("Periskope Suggestion", {"token_hash": _hash(token)}) if token else None
	if not name:
		return {"ok": False, "message": _("This link is not valid.")}
	doc = frappe.get_doc("Periskope Suggestion", name)
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
	gap = frappe.db.get_single_value("Periskope Settings", "suggestion_email_gap_hours")
	gap = 6 if gap is None else int(gap)
	last = query.latest("Periskope Suggestion", "emailed_on")
	if not force and last and get_datetime(last) > now_datetime() - timedelta(hours=gap):
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
	frappe.sendmail(
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
