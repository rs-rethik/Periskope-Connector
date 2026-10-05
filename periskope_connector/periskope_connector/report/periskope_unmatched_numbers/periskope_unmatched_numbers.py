from periskope_connector.unmatched import columns, get_rows


def execute(filters=None):
	filters = filters or {}
	return columns(), get_rows(filters.get("from_date"), filters.get("to_date"))
