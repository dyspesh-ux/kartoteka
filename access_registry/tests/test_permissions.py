"""Roles of the registry users: who reads what and who changes what."""

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.install import REGISTRY_ROLES


def make_user(email, *roles):
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
	)
	user.flags.no_welcome_mail = True
	user.insert(ignore_permissions=True)
	user.add_roles(*roles)
	return email


class TestRegistryRoles(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.auditor = make_user("auditor@registry.test", "Registry Auditor")
		self.viewer = make_user("viewer@registry.test", "Access Catalog Viewer")
		self.role_manager = make_user("roles@registry.test", "Access Role Manager")
		self.process_manager = make_user("process@registry.test", "Process Manager")
		self.admin = make_user("admin@registry.test", "Registry Admin")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def can(self, user, doctype, ptype="read"):
		return frappe.has_permission(doctype, ptype, user=user)

	def permlevels(self, user, doctype, ptype="read"):
		frappe.set_user(user)
		try:
			return set(frappe.get_meta(doctype).get_permlevel_access(ptype))
		finally:
			frappe.set_user("Administrator")

	def test_roles_exist(self):
		for role in REGISTRY_ROLES:
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_read_access(self):
		for doctype in (
			"Person",
			"IB User",
			"AD Account",
			"B24 User",
			"Access Role",
			"Business Process",
			"Sync Log",
		):
			self.assertTrue(self.can(self.auditor, doctype), doctype)
			self.assertFalse(self.can(self.auditor, doctype, "write"), doctype)
		# birth dates and write-back values: auditor yes, controller no
		self.assertIn(1, self.permlevels(self.auditor, "Person"))
		self.assertNotIn(1, self.permlevels(self.viewer, "Person"))
		self.assertTrue(self.can(self.viewer, "Person"))
		self.assertTrue(self.can(self.viewer, "Access Role"))
		self.assertFalse(self.can(self.viewer, "HR Absence"))
		# absences from Bitrix24 are the same personal data as the HR ones
		for user in (self.viewer, self.role_manager):
			self.assertFalse(self.can(user, "B24 Absence"), user)
		self.assertTrue(self.can(self.auditor, "B24 Absence"))
		self.assertFalse(self.can(self.viewer, "B24 Write Log"))
		self.assertFalse(self.can(self.viewer, "Sync Log"))

	def test_write_access(self):
		self.assertTrue(self.can(self.role_manager, "Access Role", "create"))
		self.assertTrue(self.can(self.role_manager, "SoD Rule", "write"))
		self.assertFalse(self.can(self.role_manager, "Business Process", "create"))
		self.assertTrue(self.can(self.process_manager, "Business Process", "create"))
		self.assertTrue(self.can(self.process_manager, "Process Participant", "create"))
		self.assertFalse(self.can(self.process_manager, "Access Role", "create"))
		self.assertFalse(self.can(self.viewer, "Access Role", "write"))
		for doctype in ("Info Base", "AD Domain", "B24 Portal", "Person", "Access Role"):
			self.assertTrue(self.can(self.admin, doctype, "write"), doctype)
		self.assertIn(1, self.permlevels(self.admin, "Person", "write"))

	def test_actions(self):
		from access_registry.access_roles import api

		frappe.set_user(self.viewer)
		self.assertRaises(frappe.PermissionError, api.create_draft_roles)
		frappe.set_user(self.role_manager)
		self.assertIn("message", api.create_draft_roles(min_people=100))

	def test_reports_and_pages(self):
		roles = {
			r.parent: set()
			for r in frappe.get_all("Has Role", filters={"parenttype": "Report"}, fields=["parent"])
		}
		for r in frappe.get_all("Has Role", filters={"parenttype": "Report"}, fields=["parent", "role"]):
			roles[r.parent].add(r.role)
		for report in frappe.get_all(
			"Report",
			filters={
				"module": [
					"in",
					["Access Catalog", "Active Directory", "Bitrix24", "Access Roles", "Business Processes"],
				]
			},
			pluck="name",
		):
			self.assertIn("Registry Admin", roles.get(report, set()), report)
			self.assertIn("Registry Auditor", roles.get(report, set()), report)
		page_roles = set(frappe.get_all("Has Role", filters={"parent": "access-overview"}, pluck="role"))
		self.assertTrue({"Registry Auditor", "Access Catalog Viewer"} <= page_roles)
