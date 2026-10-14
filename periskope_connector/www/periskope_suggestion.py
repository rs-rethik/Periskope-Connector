import json

import frappe

from periskope_connector.suggestions import describe_token

no_cache = 1


def get_context(context):
	token = frappe.form_dict.get("token") or ""
	decision = frappe.form_dict.get("decision") or ""
	context.no_cache = 1
	context.title = "Periskope suggestion"
	context.token = token
	context.decision = decision if decision in ("approve", "reject") else ""
	context.suggestion = describe_token(token)
	if context.suggestion:
		try:
			context.payload_text = json.dumps(json.loads(context.suggestion.payload or "{}"), indent=1)
		except ValueError:
			context.payload_text = context.suggestion.payload or ""
