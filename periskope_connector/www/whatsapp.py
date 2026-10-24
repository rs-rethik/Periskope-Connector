"""The WhatsApp inbox app at /whatsapp (built from frontend/ into whatsapp.html by `bench build`).

Open to sales users, sales managers and the admins, without Desk. The page carries the boot data the
app starts with; everything else comes from api/inbox.py, which checks access on every call.
"""

import frappe
from frappe import _

from periskope_connector import access

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + frappe.utils.quote(
			frappe.request.path if frappe.request else "/whatsapp"
		)
		raise frappe.Redirect
	if not access.can_use_inbox():
		frappe.throw(_("You do not have access to the WhatsApp inbox."), frappe.PermissionError)
	frappe.db.commit()
	context.boot = get_boot()
	return context


@frappe.whitelist(methods=["POST"])
def get_context_for_dev():
	if not frappe.conf.developer_mode:
		frappe.throw(_("This method is only meant for developer mode"))
	access.require_inbox_user()
	return get_boot()


def get_boot() -> dict:
	from periskope_connector.api.inbox import boot

	return frappe._dict(
		{
			"site_name": frappe.local.site,
			"socketio_port": frappe.conf.socketio_port,
			"csrf_token": frappe.sessions.get_csrf_token(),
			"inbox_boot": boot(),
		}
	)
