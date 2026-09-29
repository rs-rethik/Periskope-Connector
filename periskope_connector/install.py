from periskope_connector.default_tags import create_default_tags


def after_install():
	create_default_tags()
