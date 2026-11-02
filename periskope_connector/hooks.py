app_name = "periskope_connector"
app_title = "Periskope Connector"
app_publisher = "Aerele Technologies"
app_description = (
	"A Frappe connector for integrating and synchronizing WhatsApp communication through Periskope."
)
app_email = "hello@aerele.in"
app_license = "agpl-3.0"

# Apps
# ------------------

# Only Frappe CRM is required. ERPNext is optional: the Sales Person link on a
# phone and the Supplier/Employee ignore lookups appear only when it is installed.
required_apps = ["crm"]

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "periskope_connector",
		"logo": "/assets/periskope_connector/images/logo.svg",
		"title": "Periskope Connector",
		"route": "/whatsapp",
		"has_permission": "periskope_connector.api.permission.has_app_permission",
	}
]

# Installation
# ------------

# The WhatsApp inbox app (frontend/, built into www/whatsapp.html).
website_route_rules = [
	{"from_route": "/whatsapp/sw.js", "to_route": "whatsapp_sw"},
	{"from_route": "/whatsapp/manifest.webmanifest", "to_route": "whatsapp_manifest"},
	{"from_route": "/whatsapp/<path:app_path>", "to_route": "whatsapp"},
]

# the inbox's service worker, served inside /whatsapp/ (pwa.py)
# and its install manifest, with the name and icon from Settings > Branding (branding.py)
page_renderer = [
	"periskope_connector.pwa.ServiceWorkerRenderer",
	"periskope_connector.branding.ManifestRenderer",
]

after_install = "periskope_connector.install.after_install"
after_migrate = "periskope_connector.install.after_migrate"
before_uninstall = "periskope_connector.install.before_uninstall"

# Keep the normalised phone fields of leads and deals up to date when sales edits
# a number, so message matching always runs on indexed equality.
doc_events = {
	# Test mode: drop every email recipient that is not on the allow list (safety.py).
	"Email Queue": {"before_insert": "periskope_connector.safety.filter_email_queue"},
	# A rep marking the WhatsApp reply task done closes the reply reminder.
	"CRM Task": {"on_update": "periskope_connector.writeback.on_task_update"},
	"CRM Lead": {
		"after_insert": "periskope_connector.linking.update_phone_fields",
		"on_update": "periskope_connector.linking.update_phone_fields",
	},
	"CRM Deal": {
		"after_insert": "periskope_connector.linking.update_phone_fields",
		"on_update": "periskope_connector.linking.update_phone_fields",
	},
	# Meta WhatsApp (meta.py): read the connected account's messages from frappe_whatsapp, never change
	# them. Nothing runs unless a WhatsApp Account is connected in Periskope Settings.
	"WhatsApp Message": {
		"after_insert": "periskope_connector.meta.on_insert",
		"on_update": "periskope_connector.meta.on_update",
	},
}

# The WhatsApp inbox: a sales user sees only the chats on their numbers that are theirs (access.py).
permission_query_conditions = {
	"Periskope Chat": "periskope_connector.access.chat_query",
	"Periskope Message": "periskope_connector.access.message_query",
}
has_permission = {
	"Periskope Chat": "periskope_connector.access.chat_permission",
	"Periskope Message": "periskope_connector.access.message_permission",
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Conversation SLA engine (reminders, escalation tiers, high-severity fast path);
		# only acts inside the configured working hours.
		"*/10 * * * *": ["periskope_connector.reminders.run"],
		# Classify up to 20 waiting customer messages with the AI.
		"*/5 * * * *": [
			"periskope_connector.ai.classifier.run_batch",
			# The delivery ticks of inbox replies where no webhook arrives.
			"periskope_connector.api.compose.refresh_ticks",
			# Alert the assignee and the managers once when a customer waits past the SLA reply time.
			"periskope_connector.sla.check_breaches",
		],
		# The background learning agent: reads new conversations, builds the knowledge base and
		# emails suggested changes for approval (nothing is applied without a manager's approval).
		"*/15 * * * *": [
			"periskope_connector.agent.run",
			# The connection state of every WhatsApp number; managers hear when one drops.
			"periskope_connector.numbers.health_check",
		],
		# Send the AI replies that are due (after the delay; a rep's reply or a new message moves or cancels them).
		"* * * * *": [
			"periskope_connector.autoreply.run_due",
			# Send again the inbox replies that never reached Periskope (no connection, busy).
			"periskope_connector.api.compose.retry_failed",
		],
		# Re-queue webhook logs whose background job never ran (dead-letter recovery).
		"*/30 * * * *": ["periskope_connector.messages.sweep_unprocessed"],
		# Read the chat history from Periskope and save messages a missed webhook never delivered.
		"15 * * * *": ["periskope_connector.backfill.run"],
		# Once a day the AI searches the web for today's festival (the Today page's festive look).
		"5 * * * *": ["periskope_connector.ai.festival.refresh"],
	},
	"daily": [
		# Delete the public copies of files sent to Meta WhatsApp, once Meta has fetched them.
		"periskope_connector.meta.remove_shared_files",
		# Yesterday's unmatched contacts to the sales head, ranked by activity.
		"periskope_connector.contacts.send_digest",
		# Per-phone message counts: alert when a linked number goes silent.
		"periskope_connector.health.sync_check",
		# Monthly AI token budget check (also enforced before each classification batch).
		"periskope_connector.health.spend_check",
	],
	"weekly": [
		# The numbers and groups that could not be matched to a lead or deal, for sales to add.
		"periskope_connector.unmatched.send_weekly_report",
	],
	"monthly": [
		# Clear raw payloads (and optionally bodies) older than the retention settings.
		"periskope_connector.retention.run",
	],
}

# Log Clearing
# ------------

default_log_clearing_doctypes = {
	"Periskope Webhook Log": 30,  # days to retain logs
	"Periskope Agent Action": 90,
}
