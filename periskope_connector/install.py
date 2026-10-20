"""Install-time setup, so the app is plug-and-play: no manual DocType creation, roles or records.

Everything here is idempotent and also runs from `after_migrate`, so an upgrade (or a skipped
step) cannot leave a site half set up.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLE = "WhatsApp Manager"

# The normalised, indexed phone fields used for message matching (see linking.py). Hidden and
# read-only: sales keeps using the CRM's own mobile_no; the app maintains these underneath.
CUSTOM_FIELDS = {
	"CRM Lead": [
		dict(
			fieldname="periskope_phone_e164",
			label="Periskope Phone (E.164)",
			fieldtype="Data",
			insert_after="mobile_no",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
		dict(
			fieldname="periskope_phone_key",
			label="Periskope Phone Key (last 10)",
			fieldtype="Data",
			insert_after="periskope_phone_e164",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
	],
	"CRM Deal": [
		dict(
			fieldname="periskope_phone_e164",
			label="Periskope Phone (E.164)",
			fieldtype="Data",
			insert_after="mobile_no",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
		dict(
			fieldname="periskope_phone_key",
			label="Periskope Phone Key (last 10)",
			fieldtype="Data",
			insert_after="periskope_phone_e164",
			read_only=1,
			hidden=1,
			search_index=1,
			print_hide=1,
		),
	],
}


def after_install():
	setup()


def after_migrate():
	setup()


def _ensure_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)


def setup():
	"""Create the role, custom fields, lead source, starter records and default settings. Safe to
	re-run. Each step runs on its own: one that fails (say, settings an admin left invalid) is logged
	and the rest still run, so test mode and the defaults are never skipped because of it."""
	for step in (
		_ensure_role,
		_ensure_custom_fields,
		_ensure_lead_source,
		_ensure_tags,
		_ensure_working_hours,
		_ensure_webhook_events,
		_ensure_assistant_defaults,
		_ensure_crm_ui,
		_ensure_dashboard_block,
		_ensure_phone_fields,
	):
		try:
			step()
		except Exception:
			# A half-installed site must still be able to migrate; the gap shows in Health.
			frappe.log_error(title=f"Periskope: install step {step.__name__} failed")


def before_uninstall():
	"""Remove what the app added to other doctypes. Data doctypes are left for the admin."""
	try:
		from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields

		# Frappe's own helper takes the same {doctype: [fields]} shape the install uses.
		delete_custom_fields(CUSTOM_FIELDS)
	except Exception:
		frappe.log_error(title="Periskope: before-uninstall cleanup failed")


def _ensure_role():
	if frappe.db.exists("Role", ROLE):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": ROLE,
			"desk_access": 1,
			"disabled": 0,
		}
	).insert(ignore_permissions=True)


def _ensure_lead_source():
	if not frappe.db.exists("DocType", "CRM Lead Source"):
		return
	from periskope_connector.leads import ensure_lead_source

	ensure_lead_source()


def _ensure_webhook_events():
	"""Pre-select the usual events on a new site; the admin can change the selection afterwards."""
	from periskope_connector.webhooks import DEFAULT_EVENTS

	settings = frappe.get_doc("Periskope Settings")
	if settings.webhook_events:
		return
	for event in DEFAULT_EVENTS:
		settings.append("webhook_events", {"event": event})
	settings.flags.ignore_permissions = True
	settings.save()


def _ensure_assistant_defaults():
	"""Single doctypes do not apply field defaults until saved, so store the starting values once.
	Auto reply starts off: the AI does everything else on its own, and answering customers is the
	one thing the team switches on."""
	if not _stored("reply_delay_minutes"):
		frappe.db.set_single_value("Periskope Settings", "reply_delay_minutes", 15)
	# Test mode starts on, with only the owner's own number and the approvers allowed. It is seeded
	# once: an admin who switches it off later is not overridden by the next migrate.
	if not _stored("test_mode"):
		frappe.db.set_single_value(
			"Periskope Settings",
			{
				"test_mode": 1,
				"test_allowed_chats": "916374955805",
				"test_allowed_emails": "rethik@praneraservices.com\npriyadharshini@agamnext.com",
			},
		)


def _ensure_phone_fields():
	"""Fill the normalised phone fields of the leads and deals that existed before the app (saves keep
	them up to date afterwards), then link the messages that found no lead because of it."""
	from periskope_connector import linking

	filled = sum(linking.backfill_phone_fields(doctype) for doctype in ("CRM Lead", "CRM Deal"))
	if filled:
		linking.link_unlinked()


def _stored(field: str) -> bool:
	"""Whether Periskope Settings has a value stored for `field`, 0 included. (frappe.db.exists cannot
	tell: Singles is a table, not a doctype, so it always answered no and every migrate re-seeded.)"""
	singles = frappe.qb.DocType("Singles")
	return bool(
		frappe.qb.from_(singles)
		.select(singles.field)
		.where((singles.doctype == "Periskope Settings") & (singles.field == field))
		.run()
	)


def _ensure_tags():
	"""Create the starting tags on a site that has none. After that the team owns the list."""
	from periskope_connector.tags import DASHBOARD_GROUPS, DEFAULT_TAGS, FALLBACK_DEFAULT

	if frappe.db.count("Periskope Tag"):
		return
	for order, (name, description) in enumerate(DEFAULT_TAGS, start=1):
		frappe.get_doc(
			{
				"doctype": "Periskope Tag",
				"tag_name": name,
				"description": description,
				"active": 1,
				"is_default": 1 if name == FALLBACK_DEFAULT else 0,
				"sort_order": order * 10,
				"dashboard_group": DASHBOARD_GROUPS.get(name, ""),
			}
		).insert(ignore_permissions=True)


def _ensure_working_hours():
	"""A Time field's default is not applied to a new single doctype: the form fills in the current
	time instead, which made the working window zero-length. Store the usual hours up front."""
	for field, value in (("working_hours_start", "09:00:00"), ("working_hours_end", "19:00:00")):
		if not frappe.db.get_single_value("Periskope Settings", field):
			frappe.db.set_single_value("Periskope Settings", field, value)


def _ensure_crm_ui():
	"""The Periskope WhatsApp account, so the CRM shows its WhatsApp tab."""
	from periskope_connector.mirror import ensure_account

	ensure_account()


# The workspace's Messages by Tag block: every tag with its full name, a bar and the count of the last 30
# days (dashboard.tag_counts). A chart cannot do this: Frappe's charts cut long tag names short.
DASHBOARD_BLOCK = "Periskope Messages by Tag"
DASHBOARD_BLOCK_HTML = """<div class="pk-tags">
	<div class="pk-head"><span class="pk-title">Messages by Tag</span><span class="pk-sub">last 30 days</span></div>
	<div class="pk-rows"><div class="pk-empty">Loading...</div></div>
</div>"""
DASHBOARD_BLOCK_CSS = """
.pk-tags{border:1px solid var(--border-color);border-radius:var(--border-radius-lg,12px);background:var(--card-bg);padding:16px 18px;height:100%;box-sizing:border-box}
.pk-head{display:flex;align-items:baseline;gap:8px;margin-bottom:14px}
.pk-title{font-size:var(--text-base,14px);font-weight:600;color:var(--heading-color,var(--text-color))}
.pk-sub{font-size:var(--text-sm,12px);color:var(--text-muted)}
.pk-row{display:grid;grid-template-columns:170px minmax(0,1fr) 44px;align-items:center;gap:14px;padding:7px 6px;border-radius:6px;cursor:pointer}
.pk-row:hover{background:var(--fg-hover-color,var(--control-bg))}
.pk-name{font-size:13px;color:var(--text-color);overflow-wrap:anywhere}
.pk-track{display:block;height:8px;border-radius:4px;background:var(--control-bg,#2a2a2a)}
.pk-bar{display:block;height:8px;border-radius:4px;background:var(--blue-500,#5e64ff);min-width:2px}
.pk-bar.zero{min-width:0}
.pk-count{font-size:13px;text-align:right;color:var(--text-color);font-variant-numeric:tabular-nums}
.pk-empty{font-size:13px;color:var(--text-muted)}
"""
DASHBOARD_BLOCK_JS = """
const rows = root_element.querySelector(".pk-rows");
frappe.call("periskope_connector.dashboard.tag_counts").then((r) => {
	const data = r.message || [];
	const top = Math.max(1, ...data.map((d) => d.count));
	if (!data.length) {
		rows.innerHTML = `<div class="pk-empty">${__("No tags yet")}</div>`;
		return;
	}
	rows.innerHTML = "";
	data.forEach((d) => {
		const row = document.createElement("div");
		row.className = "pk-row";
		row.title = __("Open the messages tagged {0}", [d.label]);
		row.innerHTML = `<span class="pk-name"></span><span class="pk-track"><span class="pk-bar${d.count ? "" : " zero"}" style="width:${(100 * d.count) / top}%"></span></span><span class="pk-count">${d.count}</span>`;
		row.querySelector(".pk-name").textContent = d.label.charAt(0).toUpperCase() + d.label.slice(1);
		row.addEventListener("click", () =>
			frappe.set_route("List", "Periskope Message", { ai_category: d.tag, direction: "Incoming" })
		);
		rows.appendChild(row);
	});
});
"""
DASHBOARD_ROLES = ("System Manager", "Sales Manager", "WhatsApp Manager")


def _ensure_dashboard_block():
	values = {
		"html": DASHBOARD_BLOCK_HTML,
		"style": DASHBOARD_BLOCK_CSS,
		"script": DASHBOARD_BLOCK_JS,
		"private": 0,
	}
	if frappe.db.exists("Custom HTML Block", DASHBOARD_BLOCK):
		doc = frappe.get_doc("Custom HTML Block", DASHBOARD_BLOCK)
		doc.update(values)
	else:
		# named by prompt: the name goes in __newname
		doc = frappe.get_doc({"doctype": "Custom HTML Block", "__newname": DASHBOARD_BLOCK, **values})
	doc.set("roles", [{"role": role} for role in DASHBOARD_ROLES])
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
