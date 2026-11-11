"""Register this site's webhook in Periskope through its API, so nobody has to do it by hand.

Periskope keeps one subscription per event, so we make sure each event we use has one pointing at
this site's webhook URL. The signing secret is not available through the API: it is generated on
the Periskope webhook screen and has to be pasted into Periskope Settings.
"""

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import get_url

from periskope_connector.client import request

# Starting selection for a new site. The user's own choice lives in Periskope Settings > Webhook Events.
# The inbox shows delivery ticks and reactions, so it needs the ack and reaction events too.
DEFAULT_EVENTS = [
	"message.created",
	"message.updated",
	"message.deleted",
	"message.ack.updated",
	"reaction.created",
	"reaction.updated",
	"org.phone.connected",
	"org.phone.disconnected",
]
WEBHOOK_METHOD = "periskope_connector.api.webhook.receive"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def site_url() -> str:
	"""The public address of the site. A configured host_name is used as it is: Frappe's get_url()
	adds the dev port to it on a bench without supervisor, which breaks a tunnel address."""
	host = (frappe.conf.host_name or "").strip().rstrip("/")
	if host:
		return host if host.startswith(("http://", "https://")) else f"http://{host}"
	return get_url()


def webhook_url() -> str:
	return f"{site_url()}/api/method/{WEBHOOK_METHOD}"


def _listed(data) -> list[dict]:
	return data if isinstance(data, list) else (data or {}).get("webhooks", [])


def selected_events() -> list[str]:
	"""The events chosen in Periskope Settings, without duplicates, in the order they were added."""
	rows = frappe.get_all(
		"Periskope Webhook Event",
		filters={"parent": "Periskope Settings"},
		pluck="event",
		order_by="idx asc",
	)
	return list(dict.fromkeys(row for row in rows if row))


def ensure_webhooks() -> dict:
	"""Create the missing webhook subscriptions. Safe to run again: existing ones are left alone.

	Returns what was created, what already existed, and any older subscriptions that point at a
	different address for this endpoint (a previous tunnel, say), which are reported but not removed.
	"""
	url = webhook_url()
	parsed = urlparse(url)
	host = parsed.hostname or ""
	if parsed.scheme != "https" or host in LOCAL_HOSTS or "." not in host:
		frappe.throw(
			_(
				"The site address {0} is not a public https address, so Periskope cannot call it. "
				"Set the site's public address (host_name) first."
			).format(url)
		)

	events = selected_events()
	if not events:
		frappe.throw(_("Choose at least one event under Webhook Events in Periskope Settings."))

	existing = _listed(request("GET", "webhooks"))
	have = {
		w.get("integration_name") for w in existing if w.get("hook_url") == url and w.get("is_subscribed")
	}
	missing = [event for event in events if event not in have]

	created = []
	if missing:
		rows = request("POST", "webhooks", json={"hookUrl": url, "integrationName": missing})
		created = [row.get("integration_name") for row in (rows if isinstance(rows, list) else [rows])]

	stale = sorted(
		{
			w["hook_url"]
			for w in existing
			if w.get("is_subscribed")
			and (w.get("hook_url") or "").endswith(f"/api/method/{WEBHOOK_METHOD}")
			and w.get("hook_url") != url
		}
	)
	return {"url": url, "created": created, "existing": sorted(have & set(events)), "stale": stale}
