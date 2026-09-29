"""Tests of suppressed alerts: hide an alert with a reason, the journal, return, expiry and permissions."""

import frappe
from frappe.utils import add_days, today

from access_registry.access_roles import suppression
from access_registry.registry import api
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_registry_app import RegistryFixture


class TestAlertSuppression(RegistryFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Alert Suppression")
		frappe.db.delete("Version", {"ref_doctype": "Alert Suppression"})

	def unlinked_b24(self):
		rows = api.control("unlinked")["rows"]
		return next(r for r in rows if r["system"] == "Битрикс24")

	def test_suppress_hides_alert_and_keeps_journal(self):
		before = api.dashboard(refresh=1)["unlinked"]["Битрикс24"]
		row = self.unlinked_b24()
		self.assertTrue(row["alert_key"].startswith("unlinked|B24 User|"))

		n = api.suppress_alerts(
			"unlinked", [row["alert_key"]], "Учётка подрядчика ООО «Ромашка», договор 15/26"
		)
		self.assertEqual(n, 1)
		data = api.control("unlinked")
		self.assertNotIn(row["alert_key"], [r["alert_key"] for r in data["rows"]])
		self.assertEqual(data["suppressed"], 1)
		hidden = api.control("unlinked", show_suppressed=1)["rows"]
		self.assertEqual([r["alert_key"] for r in hidden], [row["alert_key"]])
		self.assertIn("Ромашка", hidden[0]["suppression_reason"])
		self.assertEqual(api.dashboard(refresh=1)["unlinked"]["Битрикс24"], before - 1)

		entry = frappe.get_doc("Alert Suppression", hidden[0]["suppression"])
		self.assertEqual((entry.status, entry.alert_title), ("Погашено", "Учётки без сотрудника"))
		self.assertEqual((entry.ref_doctype, entry.ref_name), ("B24 User", row["ref"]))
		self.assertEqual(entry.suppressed_by, "Administrator")
		self.assertIn("Битрикс24", entry.subject)

		# a second suppression of the same alert does not duplicate the journal
		self.assertEqual(api.suppress_alerts("unlinked", [row["alert_key"]], "ещё раз"), 0)

	def test_restore_returns_alert_and_is_journaled(self):
		row = self.unlinked_b24()
		api.suppress_alerts("unlinked", [row["alert_key"]], "подрядчик")
		name = frappe.get_all("Alert Suppression", pluck="name")[0]
		self.assertRaises(frappe.ValidationError, api.restore_alert, name, " ")
		api.restore_alert(name, "договор с подрядчиком закрыт")
		self.assertIn(row["alert_key"], [r["alert_key"] for r in api.control("unlinked")["rows"]])
		entry = frappe.get_doc("Alert Suppression", name)
		self.assertEqual((entry.status, entry.restored_by), ("Возвращено", "Administrator"))
		self.assertEqual(entry.restore_reason, "договор с подрядчиком закрыт")
		self.assertTrue(
			frappe.get_all("Version", filters={"ref_doctype": "Alert Suppression", "docname": name})
		)
		journal = api.control("journal")["rows"]
		self.assertEqual(journal[0]["status"], "Возвращено")
		self.assertIn("договор с подрядчиком закрыт", journal[0]["returned"])
		# it can be suppressed again: a new journal entry
		api.suppress_alerts("unlinked", [row["alert_key"]], "новый договор")
		self.assertEqual(frappe.db.count("Alert Suppression"), 2)

	def test_reason_is_required_and_keys_are_checked(self):
		row = self.unlinked_b24()
		self.assertRaises(frappe.ValidationError, api.suppress_alerts, "unlinked", [row["alert_key"]], "  ")
		self.assertRaises(
			frappe.ValidationError, api.suppress_alerts, "unlinked", ["unlinked|B24 User|nope"], "x"
		)
		self.assertRaises(frappe.ValidationError, api.suppress_alerts, "excess", ["excess|x"], "x")
		self.assertRaises(
			frappe.ValidationError,
			api.suppress_alerts,
			"unlinked",
			[row["alert_key"]],
			"x",
			add_days(today(), -1),
		)
		self.assertEqual(frappe.db.count("Alert Suppression"), 0)

	def test_valid_to_expires(self):
		row = self.unlinked_b24()
		api.suppress_alerts("unlinked", [row["alert_key"]], "до конца проекта", add_days(today(), 10))
		self.assertEqual(api.control("unlinked")["suppressed"], 1)
		name = frappe.get_all("Alert Suppression", pluck="name")[0]
		frappe.db.set_value("Alert Suppression", name, "valid_to", add_days(today(), -1))
		# past the date the alert is back at once, and the nightly job marks the entry
		self.assertEqual(api.control("unlinked")["suppressed"], 0)
		suppression.expire()
		self.assertEqual(frappe.db.get_value("Alert Suppression", name, "status"), "Истёк срок")

	def test_other_lists_and_dismissed_counter(self):
		dismissed = api.control("dismissed")["rows"]
		person = dismissed[0]["person"]
		keys = [r["alert_key"] for r in dismissed if r["person"] == person]
		api.suppress_alerts("dismissed", keys, "учётки заблокированы вручную, ждём выгрузку")
		d = api.dashboard(refresh=1)
		self.assertEqual(d["dismissed_access"]["people"], 1)
		self.assertEqual(d["suppressed"], len(keys))
		sod = api.control("sod")["rows"]
		api.suppress_alerts("sod", [sod[0]["alert_key"]], "совмещение согласовано директором")
		self.assertEqual(api.dashboard(refresh=1)["sod"], 0)

	def test_journal_is_read_only(self):
		row = self.unlinked_b24()
		api.suppress_alerts("unlinked", [row["alert_key"]], "подрядчик")
		doc = frappe.get_doc("Alert Suppression", frappe.get_all("Alert Suppression", pluck="name")[0])
		doc.reason = "переписали"
		self.assertRaises(frappe.ValidationError, doc.save, ignore_permissions=True)
		self.assertRaises(
			frappe.ValidationError, frappe.delete_doc, "Alert Suppression", doc.name, ignore_permissions=True
		)

	def test_who_can_suppress(self):
		row = self.unlinked_b24()
		viewer = make_user("viewer-supp@registry.test", "Access Catalog Viewer")
		auditor = make_user("auditor-supp@registry.test", "Registry Auditor")
		frappe.set_user(viewer)
		self.assertTrue(api.control("unlinked")["rows"])
		self.assertFalse(api.bootstrap()["can"]["suppress"])
		self.assertRaises(frappe.PermissionError, api.suppress_alerts, "unlinked", [row["alert_key"]], "x")
		frappe.set_user(auditor)
		self.assertTrue(api.bootstrap()["can"]["suppress"])
		self.assertEqual(api.suppress_alerts("unlinked", [row["alert_key"]], "подрядчик"), 1)
		self.assertEqual(frappe.db.get_value("Alert Suppression", {}, "suppressed_by"), auditor)
