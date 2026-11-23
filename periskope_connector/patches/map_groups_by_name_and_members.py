"""Groups taken for team groups get another look with the new matching: plural-tolerant names, and a
member who is a customer contact when the group name resembles that customer (linking.map_groups).
Groups already mapped, by hand or otherwise, are not touched, so running it again changes nothing."""

from periskope_connector.groups import map_groups


def execute():
	map_groups(limit=5000)
