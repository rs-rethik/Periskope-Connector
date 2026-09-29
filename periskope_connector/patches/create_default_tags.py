from periskope_connector.default_tags import create_default_tags


# Frappe runs execute() by name from patches.txt on `bench migrate` (dynamic dispatch),
# so nothing in this app calls it directly. Do not remove it: it would break migrations.
def execute():
	create_default_tags()
