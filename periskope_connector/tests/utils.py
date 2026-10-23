"""Helpers shared by the doctype tests."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import frappe

from periskope_connector import reminders

# Doctypes of other apps that ours link to. The tests make their own records, so the test runner must
# not make these: on Frappe v16 with ERPNext that walks User > Email Account > Company > ... into
# doctypes of apps that may not be installed (Payment Gateway). Doctype test modules set this as
# IGNORE_TEST_RECORD_DEPENDENCIES (v16) and test_ignore (v15).
# Our own inbox doctypes that link out (Periskope Team to users and an Assignment Rule) are listed too:
# they have no test records of their own, so the walk must stop at them.
OTHER_APP_DOCTYPES = [
	"Assignment Rule",
	"Periskope SLA Policy",
	"Periskope Team",
	"CRM Deal",
	"CRM Lead",
	"CRM Organization",
	"CRM Task",
	"FCRM Note",
	"Holiday List",
	"Sales Person",
	"ToDo",
	"User",
]

# A team group, used by the tests that need a chat with no single customer.
GROUP_CHAT = "1203639999@g.us"

# A Tuesday far in the future, so the site's real messages are outside the reminder look-back window.
TUE = datetime(2031, 9, 1)
while TUE.weekday() != 1:
	TUE += timedelta(days=1)


def event(**data):
	"""A Periskope message.created webhook payload; pass fields of `data` to override."""
	base = {
		"message_id": "link_1",
		"chat_id": "919600000001@c.us",
		"from_me": False,
		"org_phone": "919786387999@c.us",
		"sender_phone": "919600000001@c.us",
		"message_type": "chat",
		"body": "Hi, what is the price?",
		"timestamp": "2026-09-29T06:30:00Z",
	}
	base.update(data)
	return {"event_type": "message.created", "data": base}


def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
	return TUE + timedelta(days=day_offset, hours=hour, minutes=minute)


def cfg(**kw):
	"""A reminder config for a test: working hours 09:00-19:00 Monday to Saturday, the usual SLA ladder."""
	base = {
		"enabled": True,
		"start": datetime(2000, 1, 1, 9, 0).time(),
		"end": datetime(2000, 1, 1, 19, 0).time(),
		"days": reminders.WEEKDAYS["Monday to Saturday"],
		"holidays": set(),
		"emails": ["boss@example.com"],
		"md_emails": ["md@example.com"],
		"groups": False,
		"ack_words": [],
	}
	base.update(kw)
	base.setdefault("levels", reminders.default_levels())
	return SimpleNamespace(**base)


def msg(body="", message_type="text", needs_reply=0, status="", severity=None):
	"""A message-like record for the pure reply-needed checks."""
	return frappe._dict(
		body=body,
		message_type=message_type,
		needs_reply=needs_reply,
		classification_status=status,
		severity=severity,
	)


def isolate(case, name: str = "periskope_test"):
	"""Make a test on the real site DB leave nothing behind: commits become no-ops, no email is sent,
	and the whole test is rolled back. Returns the patched frappe.sendmail."""
	from unittest.mock import patch

	commit = patch.object(frappe.db, "commit")
	commit.start()
	case.addCleanup(commit.stop)
	mail = patch.object(frappe, "sendmail")
	sent = mail.start()
	case.addCleanup(mail.stop)
	frappe.db.savepoint(name)
	case.addCleanup(frappe.db.rollback, save_point=name)
	return sent


def settings(**values):
	"""Set values on Periskope Settings for the current test (rolled back with it)."""
	for field, value in values.items():
		frappe.db.set_single_value("Periskope Settings", field, value)
