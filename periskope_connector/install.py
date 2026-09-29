import click
import frappe

from periskope_connector.default_tags import create_default_tags


def after_install():
	"""Create the default message tags.

	A failure is logged and reported but does not abort the app install: the tags are seed data
	that can be created again at any time, and messages are captured without them. It has to be
	reported clearly, because Frappe marks every patch as done on a fresh install, so a later
	`bench migrate` would not create the tags.
	"""
	# Undo only what the seeding did, not the rest of the install.
	frappe.db.savepoint("periskope_seed_tags")
	try:
		create_default_tags()
	except Exception:
		frappe.db.rollback(save_point="periskope_seed_tags")
		frappe.log_error(title="Periskope: could not create the default message tags")
		click.secho(
			"Periskope Connector: the default message tags were not created (see the Error Log). "
			"Create them with: bench --site <site> execute periskope_connector.default_tags.create_default_tags",
			fg="yellow",
		)
