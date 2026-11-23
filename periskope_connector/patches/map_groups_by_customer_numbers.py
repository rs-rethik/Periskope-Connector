"""Groups taken for team groups get another look now that a member's number also counts when it is on
an ERPNext customer's address or contact (linking.customer_organizations). Groups already mapped, by
hand or otherwise, are not touched, so running it again changes nothing."""

from periskope_connector.linking import map_groups


def execute():
	map_groups(limit=5000)
