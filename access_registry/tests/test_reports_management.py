"""Tests of the reports inside the app (section «Отчёты») and of the management dashboard («Руководству»)."""

import frappe
from frappe.utils import add_days, today

from access_registry import app_access
from access_registry.registry import api, metrics, reports
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_registry_app import RegistryFixture


class TestReportsAndManagement(RegistryFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Registry Access Member")
		frappe.db.delete("Registry Access Report")
		frappe.db.delete("Registry Access Profile")
		frappe.db.delete("Registry Metric Snapshot")
		frappe.cache().delete_value(metrics.CACHE_KEY)
		self.user = make_user("reports-user@registry.test")

	def profile(self, sections, report_list=(), personal=0):
		frappe.set_user("Administrator")
		api.save_profile(
			{
				"name": frappe.db.exists("Registry Access Profile", "Руководитель"),
				"profile_name": "Руководитель",
				"sections": sections,
				"personal": personal,
				"reports": list(report_list),
				"members": [self.user],
			}
		)
		frappe.set_user(self.user)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	# ------------------------------------------------------------------ reports

	def test_catalog_reports_exist_and_filters_parse(self):
		for name in reports.BY_NAME:
			self.assertTrue(frappe.db.exists("Report", name), name)
		filters = {f["fieldname"]: f for f in reports.parse_filters("AD Inactive Accounts")}
		self.assertTrue(filters, "filters of the JS file are read")
		for f in filters.values():
			self.assertTrue(f["label"] and f["fieldtype"])

	def test_report_subset_and_run(self):
		self.profile({"reports": 1}, ["IB Access Report", "B24 Profile Differences"])
		names = app_access.report_names()
		# the personal-data report needs the flag
		self.assertEqual(names, ["IB Access Report"])
		catalog = api.reports_catalog()
		self.assertEqual([r["name"] for g in catalog for r in g["reports"]], ["IB Access Report"])
		self.assertRaises(frappe.PermissionError, api.run_report, "AD Without Employee")
		self.assertRaises(frappe.PermissionError, api.report_meta, "B24 Profile Differences")
		data = api.run_report("IB Access Report", {})
		self.assertTrue(data["columns"])
		self.assertTrue(data["rows"])
		person_cols = [c for c in data["columns"] if c["app_link"] == "person"]
		self.assertTrue(person_cols)
		self.assertEqual(api.report_meta("IB Access Report")["title"], "Полный отчёт по доступам 1С")

		self.profile({"reports": 1}, ["IB Access Report", "B24 Profile Differences"], personal=1)
		self.assertIn("B24 Profile Differences", app_access.report_names())

	def test_all_reports_when_list_empty_and_export(self):
		self.profile({"reports": 1})
		names = app_access.report_names()
		self.assertIn("AD Without Employee", names)
		self.assertNotIn("B24 Absence Differences", names)  # personal data
		api.export_report("AD Without Employee", "{}")
		self.assertEqual(frappe.response["type"], "binary")
		self.assertTrue(frappe.response["filecontent"].startswith(b"PK"))  # xlsx is a zip

	def test_no_reports_section(self):
		self.profile({"people": 1})
		self.assertEqual(app_access.report_names(), [])
		self.assertRaises(frappe.PermissionError, api.reports_catalog)

	# ------------------------------------------------------------------ management

	def test_management_only_for_its_section(self):
		self.profile({"people": 1})
		self.assertRaises(frappe.PermissionError, api.management)
		self.profile({"management": 1})
		self.assertEqual(api.bootstrap()["can"]["sections"]["management"], 1)
		self.assertRaises(frappe.PermissionError, api.control, "dismissed")
		d = api.management(days=30)
		self.assertEqual(d["days"], 30)
		self.assertEqual(d["history"][-1]["date"], today())
		now = d["current"]
		self.assertGreaterEqual(now["dismissed_people"], 1)
		self.assertGreaterEqual(now["accounts_ad"], now["linked_ad"])
		self.assertIsNotNone(now["compliance"])
		# no names of people or accounts in the dashboard
		text = frappe.as_json(d)
		for full_name in frappe.get_all("Person", pluck="full_name"):
			self.assertNotIn(full_name, text)

	def test_snapshots_build_the_history(self):
		frappe.set_user("Administrator")
		m = metrics.collect()
		metrics.save_snapshot(add_days(today(), -40), {**m, "unlinked": 99})
		metrics.save_snapshot(add_days(today(), -3), {**m, "unlinked": 7})
		name = metrics.save_snapshot(add_days(today(), -3), {**m, "unlinked": 5})  # the day is overwritten
		self.assertEqual(frappe.db.count("Registry Metric Snapshot"), 2)
		self.assertEqual(
			frappe.parse_json(frappe.db.get_value("Registry Metric Snapshot", name, "metrics"))["unlinked"], 5
		)
		d = metrics.dashboard(30)
		self.assertEqual([p["date"] for p in d["history"]], [str(add_days(today(), -3)), today()])
		self.assertEqual(d["history"][0]["unlinked"], 5)
		self.assertEqual(len(metrics.dashboard(90)["history"]), 3)
		self.assertEqual(metrics.dashboard(12345)["days"], 30)  # unknown period → default
		metrics.scheduled()
		self.assertTrue(frappe.db.exists("Registry Metric Snapshot", {"snapshot_date": today()}))
		# today's live figures are not doubled by today's stored snapshot
		self.assertEqual(sum(1 for p in metrics.dashboard(7)["history"] if p["date"] == today()), 1)

	def test_roles_see_management(self):
		viewer = make_user("mgmt-viewer@registry.test", "Access Catalog Viewer")
		frappe.set_user(viewer)
		self.assertEqual(api.bootstrap()["can"]["sections"]["management"], 1)
		self.assertTrue(api.management()["sources"] is not None)
