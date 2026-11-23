"""Groups taken for team groups get another look now that a member's number matches a customer's
address or contact however it was typed, with spaces or dashes (linking.with_number). Groups already
mapped, by hand or otherwise, are not touched, so running it again changes nothing."""

from periskope_connector.linking import map_groups


def execute():
	map_groups(limit=5000)
