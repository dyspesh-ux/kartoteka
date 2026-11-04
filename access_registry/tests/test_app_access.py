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

	def test_profile_limited_to_systems(self):
		frappe.set_user("Administrator")
		api.save_profile(
			{
				"profile_name": "Администратор AD",
				"sections": {"overview": 1, "people": 1, "control": 1, "access": 1, "reports": 1},
				"systems": ["ad"],
				"members": [self.user],
			}
		)
		frappe.cache().delete_value(api.CACHE_KEY)
		self.as_user()
		self.assertEqual(api.bootstrap()["can"]["systems"], ["ad"])
		self.assertNotIn("shares", app_access.control_lists())
		for kind in ("dismissed", "unlinked", "stale", "privileged", "excess"):
			systems = {r.get("system") for r in api.control(kind)["rows"]}
			self.assertTrue(systems <= {"AD", "Active Directory"}, (kind, systems))
		# the person card: only AD
		frappe.set_user("Administrator")
		person = frappe.get_all("IB User", filters={"person": ["is", "set"]}, pluck="person")[0]
		self.as_user()
		card = api.person(person)
		self.assertEqual((card["ib"], card["b24"], card["shares"]), ([], [], []))
		self.assertTrue(all(r["system"] == "Active Directory" for r in card["reconciliation"]))
		self.assertTrue(all(e["system"] == "Active Directory" for e in api.entitlements()))
		self.assertFalse(
			[r for r in api.search("Ива") if r.get("subtitle", "").startswith(("1С", "Битрикс24"))]
		)
		# reports: AD only, mixed reports keep their AD rows
		names = app_access.report_names()
		self.assertIn("AD Without Employee", names)
		self.assertNotIn("IB Access Report", names)
		self.assertNotIn("B24 Section Access", names)
		self.assertRaises(frappe.PermissionError, api.run_report, "IB Access Report")
		# the overview counts AD only and sends nothing internal
		d = api.dashboard(refresh=1)
		self.assertEqual(set(d["unlinked"]) - {"AD"}, set())
		self.assertEqual(set(d["dismissed_access"]["by_system"]) - {"AD"}, set())
		self.assertFalse([k for k in d if k.startswith("_")])
		self.assertTrue(all(not s["kind"].startswith(("Права 1С", "Битрикс24")) for s in d["sources"]))

	def test_no_system_chosen_means_all(self):
		self.profile(people=1, control=1)
		self.as_user()
		self.assertEqual(app_access.systems(), list(app_access.SYSTEMS))
		self.assertIn("shares", app_access.control_lists())

	def test_guest_and_administrator(self):
		guest = app_access.access("Guest")
		self.assertFalse(any(guest["sections"].values()))
		self.assertFalse(guest["personal"])
		self.assertFalse(app_access.can_open_app("Guest"))
		self.assertEqual(app_access.report_names("Guest"), [])
		admin = app_access.access("Administrator")
		self.assertTrue((admin["admin"], admin["ad_approver"], admin["personal"]))
		expected = {
			s: app_access.WORK if s in app_access.WORK_SECTIONS else app_access.VIEW
			for s in app_access.SECTIONS
		}
		self.assertEqual(admin["sections"], expected)

	def test_role_manager_and_process_manager_roles(self):
		manager = make_user("profile-rolemanager@registry.test", "Access Role Manager")
		a = app_access.access(manager)
		self.assertEqual(
			(a["sections"]["control"], a["sections"]["roles"]), (app_access.WORK, app_access.WORK)
		)
		self.assertFalse(a["personal"])  # working in the role model is not personal data
		self.assertIn("роль «Access Role Manager»", a["via"])
		process = make_user("profile-process@registry.test", "Process Manager")
		a = app_access.access(process)
		self.assertTrue(all(v == app_access.VIEW for v in a["sections"].values()))
		self.assertEqual(a["sections"]["control"], app_access.VIEW)  # «Контроль» for reading only

	def test_system_allowed_and_filter_rows(self):
		self.assertTrue(app_access.system_allowed("", ["ad"]))  # rows without a system are shown
		self.assertTrue(app_access.system_allowed(None, ["ad"]))
		self.assertTrue(app_access.system_allowed("  AD  ", ["ad"]))  # trimmed
		self.assertFalse(app_access.system_allowed("1С", ["ad"]))
		self.assertFalse(app_access.system_allowed("Другое", ["ad"]))  # unknown systems: all only
		self.assertTrue(app_access.system_allowed("Другое", list(app_access.SYSTEMS)))
		rows = [{"system": "AD"}, {"system": "1С"}, {}, {"system": "Другое"}]
		self.assertEqual(app_access.filter_rows(rows, allowed=["ad"]), [rows[0], rows[2]])
		self.assertEqual(
			app_access.filter_rows(rows, allowed=list(app_access.SYSTEMS)), rows
		)  # everything for those who see all systems
		by_other_key = [{"sys": "AD"}, {"sys": "Битрикс24"}]
		self.assertEqual(len(app_access.filter_rows(by_other_key, key="sys", allowed=["ad", "b24"])), 2)

	def test_control_lists_follow_system_limits(self):
		frappe.set_user("Administrator")
		api.save_profile(
			{
				"profile_name": "Только AD (доступ)",
				"sections": {"control": 1},
				"systems": ["ad"],
				"members": [self.user],
			}
		)
		self.as_user()
		lists = app_access.control_lists()
		self.assertNotIn("shares", lists)
		self.assertNotIn("assets", lists)
		self.assertIn("dismissed", lists)  # not bound to one system
		# adding «Общие папки» opens its lists, «Техника» stays closed
		frappe.set_user("Administrator")
		name = frappe.db.get_value("Registry Access Profile", {"profile_name": "Только AD (доступ)"})
		api.save_profile(
			{
				"name": name,
				"profile_name": "Только AD (доступ)",
				"sections": {"control": 1},
				"systems": ["ad", "shares"],
				"members": [self.user],
			}
		)
		self.as_user()
		lists = app_access.control_lists()
		self.assertIn("shares", lists)
		self.assertNotIn("assets", lists)

	def test_require_section_and_control_errors(self):
		self.profile(control=1, lists=["unlinked"])
		self.as_user()
		app_access.require_section("control")  # the view level is enough
		app_access.require_control("unlinked")
		self.assertRaises(frappe.PermissionError, app_access.require_control, "dismissed")  # not its list
		self.assertRaises(frappe.PermissionError, app_access.require_control, "unlinked", app_access.WORK)
		with self.assertRaises(frappe.PermissionError) as err:
			app_access.require_section("control", app_access.WORK)
		self.assertIn("нужен уровень", str(err.exception))
		frappe.set_user("Administrator")
		self.assertRaises(frappe.ValidationError, app_access.require_control, "nope")  # unknown list first

	def test_reviewer_opens_app_for_reviews(self):
		reviewer = make_user("profile-reviewer@registry.test", "Access Reviewer")
		a = app_access.access(reviewer)
		self.assertTrue(a["reviewer"])
		self.assertFalse(any(a["sections"].values()))  # no sections, only the review assignments
		self.assertTrue(app_access.can_open_app(reviewer))

	def test_ad_approve_flag_of_profile(self):
		frappe.set_user("Administrator")
		api.save_profile(
			{
				"profile_name": "Одобряет планы AD",
				"sections": {"people": 1},
				"ad_approve": 1,
				"members": [self.user],
			}
		)
		self.as_user()
		self.assertTrue(app_access.access()["ad_approver"])

	def test_access_admin_collects_reviewers_and_skips_inactive(self):
		reviewer = make_user("admin-reviewer@registry.test", "Access Reviewer")
		role_admin = make_user("admin-role@registry.test", "Registry Admin")
		self.profile(people=1, members=[self.user])
		lonely = make_user("admin-lonely@registry.test")
		name = self.profile("Временный", people=1, members=[lonely])
		frappe.db.set_value("Registry Access Profile", name, "enabled", 0)
		frappe.clear_document_cache("Registry Access Profile", name)
		frappe.set_user("Administrator")
		users = {u["user"]: u for u in api.access_admin()["users"]}
		self.assertIn(self.user, users)
		self.assertIn(reviewer, users)
		self.assertEqual(users[reviewer]["via"], ["только свои задания пересмотра"])
		self.assertIn(role_admin, users)
		self.assertNotIn(lonely, users)  # the disabled profile gives nothing
		self.assertNotIn("Administrator", users)

	def test_find_users_search_limits_and_rights(self):
		frappe.set_user("Administrator")
		found = {u["user"] for u in api.find_users("profile-user")}
		self.assertIn(self.user, found)
		self.assertNotIn("Administrator", {u["user"] for u in api.find_users("Administrator")})
		self.assertNotIn("Guest", {u["user"] for u in api.find_users(" ")})
		self.assertLessEqual(len(api.find_users("")), 10)  # the limit of the picker
		self.as_user()
		self.assertRaises(frappe.PermissionError, api.find_users, "x")
		self.assertRaises(frappe.PermissionError, api.access_admin)
		self.assertRaises(frappe.PermissionError, api.profile_history, "anything")
