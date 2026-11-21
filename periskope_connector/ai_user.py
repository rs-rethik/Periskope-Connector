"""The "Pranera Connect AI" user: the name the app's own work is recorded under.

WhatsApp webhooks reach the app as guest calls, and the jobs they queue run as the user who queued
them, so messages, chats, files, assignments and AI tags used to be saved as "Guest". The jobs now
continue as this user instead, so the CRM, ERPNext and the timelines show "Pranera Connect AI".

The user has no password (nobody logs in as it) and one role of its own with no permissions; the
app's writes keep their own checks (system.py). An AI outside the server can later be given this
user's API key, so ERPNext knows who it is and its role limits what it may do.

The switch happens only inside background jobs, never in the web request: changing the user of a
guest request would save that session under the shared "Guest" session id.
"""

import frappe

AI_USER = "connect-ai@pranera.in"
AI_ROLE = "Pranera Connect AI"
AI_FIRST_NAME = "Pranera Connect AI"


def ensure():
	"""Create the role and the user if missing (install and migrate; idempotent)."""
	if not frappe.db.exists("Role", AI_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": AI_ROLE, "desk_access": 0}).insert(
			ignore_permissions=True  # set-up, run by the system
		)
	if frappe.db.exists("User", AI_USER):
		return
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": AI_USER,
			"first_name": AI_FIRST_NAME,
			"send_welcome_email": 0,
			"enabled": 1,
			"roles": [{"role": AI_ROLE}],
		}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)  # set-up, run by the system


def act_as_ai():
	"""In a background job a guest call queued (a webhook), continue as the AI user. Does nothing in
	a web request, for a signed-in user, or before the user exists."""
	if frappe.session.user != "Guest" or getattr(frappe.local, "request", None):
		return
	if frappe.db.exists("User", AI_USER):
		# only in a background job (checked above): the guest web session is never changed
		frappe.set_user(AI_USER)  # nosemgrep: frappe-setuser
