"""Tests of access to the sections of /registry by access profiles (in addition to the registry roles)."""

import frappe

from access_registry import app_access
from access_registry.registry import api
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_registry_app import RegistryFixture


class TestAppAccess(RegistryFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Registry Access Member")
		frappe.db.delete("Registry Access Control List")
		frappe.db.delete("Registry Access Profile")
		frappe.db.delete("Alert Suppression")
		frappe.db.delete("Version", {"ref_doctype": "Registry Access Profile"})
		# a user without any registry role: only profiles give access
		self.user = make_user("profile-user@registry.test")

	def profile(self, name="Главный бухгалтер", members=None, **sections):
		data = {
			"name": frappe.db.exists("Registry Access Profile", name),
			"profile_name": name,
			"sections": {k: v for k, v in sections.items() if k in app_access.SECTIONS},
			"personal": sections.get("personal", 0),
			"control_lists": sections.get("lists", []),
			"members": members if members is not None else [self.user],
		}
		frappe.set_user("Administrator")
		return api.save_profile(data)

	def as_user(self):
		frappe.set_user(self.user)

	def test_no_profile_no_access(self):
		self.as_user()
		self.assertFalse(app_access.can_open_app())
		self.assertRaises(frappe.PermissionError, api.bootstrap)
		self.assertRaises(frappe.PermissionError, api.people)

	def test_profile_opens_only_its_sections(self):
		self.profile(people=1, access=1)
		self.as_user()
		self.assertTrue(app_access.can_open_app())
		boot = api.bootstrap()
		self.assertTrue(boot["can"]["read"])
		self.assertEqual(boot["can"]["sections"]["people"], 1)
		self.assertEqual(boot["can"]["sections"]["control"], 0)
		self.assertIn("профиль «Главный бухгалтер»", boot["can"]["via"])
		self.assertFalse(boot["can"]["admin"])

		self.assertTrue(api.people()["rows"])
		self.assertTrue(api.entitlements() is not None)
		for call in (api.dashboard, api.roles, api.processes, api.reviews):
			self.assertRaises(frappe.PermissionError, call)
		self.assertRaises(frappe.PermissionError, api.control, "dismissed")
		self.assertRaises(frappe.PermissionError, api.access_admin)
		# search only in the open sections
		kinds = {r["kind"] for r in api.search("Ива")}
		self.assertNotIn("role", kinds)
		self.assertNotIn("process", kinds)

	def test_personal_data_only_with_the_flag(self):
		person = frappe.get_all("Person", filters={"birth_date": ["is", "set"]}, pluck="name")[0]
		self.profile(people=1)
		self.as_user()
		self.assertIsNone(api.person(person)["person"]["birth_date"])
		self.profile(people=1, personal=1, members=[self.user])
		self.as_user()
		self.assertTrue(api.person(person)["person"]["birth_date"])

	def test_control_lists_and_levels(self):
		self.profile(control=1, lists=["unlinked", "journal"])
		self.as_user()
		self.assertEqual(api.bootstrap()["can"]["control_lists"], ["unlinked", "journal"])
		rows = api.control("unlinked")["rows"]
		self.assertTrue(rows)
		self.assertRaises(frappe.PermissionError, api.control, "dismissed")
		self.assertRaises(frappe.ValidationError, api.control, "nope")
		# «Просмотр» does not suppress
		self.assertRaises(
			frappe.PermissionError, api.suppress_alerts, "unlinked", [rows[0]["alert_key"]], "x"
		)
		# the dashboard keeps only the counters of the allowed lists
		d = api.dashboard(refresh=1)
		self.assertIn("unlinked", d)
		self.assertNotIn("dismissed_access", d)
		self.assertNotIn("sod", d)

		self.profile(control=2, lists=["unlinked", "journal"], members=[self.user])
		self.as_user()
		self.assertEqual(api.suppress_alerts("unlinked", [rows[0]["alert_key"]], "подрядчик"), 1)
		# someone else suppressed a dismissed alert: not in this user's journal
		frappe.set_user("Administrator")
		other = api.control("dismissed")["rows"][0]
		api.suppress_alerts("dismissed", [other["alert_key"]], "другой список")
		self.as_user()
		journal = api.control("journal")["rows"]
		self.assertEqual({r["alert_kind"] for r in journal}, {"unlinked"})
		self.assertEqual(
			api.dashboard(refresh=1)["suppressed"], 1
		)  # the other list's suppression is not counted
		self.assertFalse(api.bootstrap()["can"]["search"])
		dismissed = frappe.get_all("Alert Suppression", filters={"alert_kind": "dismissed"}, pluck="name")[0]
		self.assertRaises(frappe.PermissionError, api.restore_alert, dismissed, "нет")

	def test_levels_add_up_and_disabled_profile(self):
		self.profile("Просмотр", people=1)
		name = self.profile("Контроль", control=2, members=[self.user])
		self.as_user()
		sections = api.bootstrap()["can"]["sections"]
		self.assertEqual((sections["people"], sections["control"]), (1, 2))
		frappe.set_user("Administrator")
		frappe.db.set_value("Registry Access Profile", name, "enabled", 0)
		frappe.clear_document_cache("Registry Access Profile", name)
		self.as_user()
		self.assertEqual(api.bootstrap()["can"]["sections"]["control"], 0)

	def test_roles_work_as_before(self):
		viewer = make_user("profile-viewer@registry.test", "Access Catalog Viewer")
		frappe.set_user(viewer)
		boot = api.bootstrap()
		self.assertTrue(all(v == 1 for v in boot["can"]["sections"].values()))
		self.assertFalse(boot["can"]["suppress"])
		self.assertFalse(boot["can"]["personal"])
		auditor = make_user("profile-auditor@registry.test", "Registry Auditor")
		frappe.set_user(auditor)
		boot = api.bootstrap()
		self.assertTrue(boot["can"]["suppress"] and boot["can"]["personal"])
		self.assertFalse(boot["can"]["exceptions"])

	def test_admin_page_and_history(self):
		name = self.profile(people=1)
		frappe.set_user("Administrator")
		self.profile(people=1, access=1, members=[self.user])
		admin = api.access_admin()
		self.assertEqual([p["profile_name"] for p in admin["profiles"]], ["Главный бухгалтер"])
		me = next(u for u in admin["users"] if u["user"] == self.user)
		self.assertEqual(me["sections"]["access"], 1)
		self.assertIn("профиль «Главный бухгалтер»", me["via"])
		history = api.profile_history(name)
		self.assertTrue(any("s_access" in h["what"] for h in history), history)
		self.assertTrue(api.find_users("profile-user"))
		self.as_user()
		self.assertRaises(frappe.PermissionError, api.save_profile, {"profile_name": "x"})

	def test_page_allowed_by_profile(self):
		from access_registry.www import registry as page

		self.profile(people=1)
		self.as_user()
		context = frappe._dict()
		page.get_context(context)
		self.assertTrue(context.allowed)
