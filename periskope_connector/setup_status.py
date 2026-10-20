"""The four setup steps shown at the top of Periskope Settings:

1. Connect Periskope: the API key works and at least one WhatsApp number is linked.
2. AI: a key (or Jarvis), a monthly token budget, and a passed AI connection test.
3. Team: approvers who can approve the agent's suggestions, and a sales rep for the follow-ups.
4. Auto reply: whether the AI answers customers itself.

The AI agent runs whenever the integration is enabled (agent_on); it needs no switch of its own.
Auto reply cannot be switched on until steps 1 to 3 are done (Periskope Settings.validate).
"""

import re

import frappe
from frappe import _

APPROVER_ROLES = {"System Manager", "WhatsApp Manager", "Sales Manager"}


def _step(key: str, title: str, ok: bool, message: str) -> dict:
	return {"key": key, "title": title, "ok": bool(ok), "message": message}


def emails(raw: str | None) -> list[str]:
	return [e.strip() for e in re.split(r"[,\n;]", raw or "") if e.strip()]


def _periskope(s) -> dict:
	title = _("Connect Periskope")
	if not s.enabled:
		return _step("periskope", title, False, _("Tick Enable Periskope Integration."))
	if not s.get_password("api_key", raise_exception=False):
		return _step("periskope", title, False, _("Enter the Periskope API key."))
	if not (s.connection_status or "").startswith("Connected"):
		return _step("periskope", title, False, _("Click Test Connection."))
	if not s.phones:
		return _step("periskope", title, False, _("No WhatsApp number is linked in Periskope yet."))
	return _step("periskope", title, True, s.connection_status)


def _ai(s) -> dict:
	from periskope_connector.ai.classifier import uses_jarvis

	title = _("AI")
	if not uses_jarvis(s) and not s.get_password("anthropic_api_key", raise_exception=False):
		return _step("ai", title, False, _("Enter the AI API key."))
	if not (s.ai_monthly_budget_tokens or 0) > 0:
		return _step("ai", title, False, _("Set a monthly token budget."))
	if not (s.ai_status or "").startswith("Working"):
		return _step("ai", title, False, _("Click Test AI."))
	return _step(
		"ai",
		title,
		True,
		_("{0}, budget {1:,} tokens a month").format(s.ai_status, s.ai_monthly_budget_tokens),
	)


def approver_problems(raw: str | None) -> list[str]:
	"""Approver emails that are not enabled users with a role that may approve."""
	problems = []
	for email in emails(raw):
		user = frappe.db.get_value("User", {"name": email, "enabled": 1})
		if not user:
			problems.append(_("{0} is not an active user").format(email))
		elif not APPROVER_ROLES & set(frappe.get_roles(user)):
			problems.append(_("{0} has no approver role").format(email))
	return problems


def _team(s) -> dict:
	title = _("Team")
	if not emails(s.approver_emails):
		return _step("team", title, False, _("Enter at least one approver email."))
	problems = approver_problems(s.approver_emails)
	if problems:
		return _step("team", title, False, "; ".join(problems))
	if not (s.default_rep or any(p.rep for p in s.phones)):
		return _step("team", title, False, _("Choose a default sales rep or a rep for a number."))
	return _step("team", title, True, _("{0} approver(s)").format(len(emails(s.approver_emails))))


def _agent(s) -> dict:
	return _step(
		"agent",
		_("Auto Reply"),
		s.enable_auto_reply,
		_("The AI answers customers a rep has not answered")
		if s.enable_auto_reply
		else _("Off: the AI does everything else, but only reps answer customers."),
	)


def get_status(settings=None) -> dict:
	s = settings or frappe.get_doc("Periskope Settings")
	steps = [_periskope(s), _ai(s), _team(s), _agent(s)]
	from periskope_connector.safety import test_mode

	return {
		"steps": steps,
		"ready": all(step["ok"] for step in steps[:3]),
		"done": all(step["ok"] for step in steps),
		"test_mode": test_mode(),
	}


def missing_before_agent(settings) -> list[str]:
	"""What still blocks switching the agent on (empty when steps 1 to 3 are done)."""
	return [
		f"{step['title']}: {step['message']}" for step in get_status(settings)["steps"][:3] if not step["ok"]
	]


def agent_on(settings=None) -> bool:
	"""The AI agent works whenever the integration is enabled; there is no other switch."""
	if settings is None:
		return bool(frappe.db.get_single_value("Periskope Settings", "enabled"))
	return bool(getattr(settings, "enabled", None))


def auto_reply_on(settings=None) -> bool:
	if settings is None:
		return agent_on() and bool(frappe.db.get_single_value("Periskope Settings", "enable_auto_reply"))
	return agent_on(settings) and bool(getattr(settings, "enable_auto_reply", None))
