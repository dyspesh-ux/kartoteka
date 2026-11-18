"""Tests of the Diadoc module: scaffold, parser, person linking, upload sync, entitlements, reminders."""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestDiadocScaffold(FrappeTestCase):
	def test_module_scaffold(self):
		self.assertTrue(frappe.db.exists("DocType", "Diadoc Organization"))
		self.assertTrue(frappe.db.exists("DocType", "Diadoc User"))
		self.assertTrue(frappe.db.exists("Role", "Diadoc Specialist"))
		ws = frappe.get_doc("Workspace", "Диадок")
		linked = {row.link_to for row in ws.links if row.link_type == "DocType"}
		self.assertIn("Diadoc Organization", linked)
		self.assertIn("Diadoc User", linked)
