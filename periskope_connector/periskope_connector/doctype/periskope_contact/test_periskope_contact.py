from frappe.tests.utils import FrappeTestCase

# The CRM link fields would otherwise make the runner build test leads and deals.
test_ignore = ["CRM Lead", "CRM Deal"]


class TestPeriskopeContact(FrappeTestCase):
	pass
