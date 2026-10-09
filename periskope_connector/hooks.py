app_name = "periskope_connector"
app_title = "Periskope Connector"
app_publisher = "Aerele Technologies"
app_description = (
	"A Frappe connector for integrating and synchronizing WhatsApp communication through Periskope."
)
app_email = "hello@aerele.in"
app_license = "mit"

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
		"route": "/app/periskope",
		"has_permission": "periskope_connector.api.permission.has_app_permission",
	}
]

# Installation
# ------------

after_install = "periskope_connector.install.after_install"
after_migrate = "periskope_connector.install.after_migrate"
before_uninstall = "periskope_connector.install.before_uninstall"

# Optional own-numbers read access for reps (off by default; see Periskope Settings).
has_permission = {
	"Periskope Message": "periskope_connector.api.permission.message_has_permission",
}

# Keep the normalised phone fields of leads and deals up to date when sales edits
# a number, so message matching always runs on indexed equality.
doc_events = {
	"CRM Lead": {
		"after_insert": "periskope_connector.linking.update_phone_fields",
		"on_update": "periskope_connector.linking.update_phone_fields",
	},
	"CRM Deal": {
		"after_insert": "periskope_connector.linking.update_phone_fields",
		"on_update": "periskope_connector.linking.update_phone_fields",
	},
}

# The Flow tab of the settings page draws the pipeline and edits its steps.
doctype_js = {"Periskope Settings": "public/js/periskope_flow.js"}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Conversation SLA engine (reminders, escalation tiers, high-severity fast path);
		# only acts inside the configured working hours.
		"*/10 * * * *": ["periskope_connector.reminders.run"],
		# Classify up to 20 waiting customer messages with the AI.
		"*/5 * * * *": ["periskope_connector.ai.classifier.run_batch"],
		# Re-queue webhook logs whose background job never ran (dead-letter recovery).
		"*/30 * * * *": ["periskope_connector.messages.sweep_unprocessed"],
		# Read the chat history from Periskope and save messages a missed webhook never delivered.
		"15 * * * *": ["periskope_connector.backfill.run"],
	},
	"daily": [
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
	"Periskope Webhook Log": 30  # days to retain logs
}
