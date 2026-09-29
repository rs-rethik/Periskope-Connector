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

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Customer reply reminders; only acts inside the configured working hours.
		"*/10 * * * *": ["periskope_connector.reminders.run"],
	},
	"hourly": [
		"periskope_connector.ai.classifier.retry_failed_classifications",
	],
}

# Log Clearing
# ------------

default_log_clearing_doctypes = {
	"Periskope Webhook Log": 30  # days to retain logs
}
