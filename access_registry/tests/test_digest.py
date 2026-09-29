"""Tests of the morning digest: what is new since the previous digest, recipients, schedule, the letter."""

import json
from unittest.mock import patch

import frappe
from frappe.utils import add_days, get_datetime, now_datetime, today

from access_registry.access_roles import digest
from access_registry.registry import api
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_registry_app import RegistryFixture


class TestMorningDigest(RegistryFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Registry Digest Log")
		frappe.db.delete("Alert Suppression")
		self.auditor = make_user("digest-auditor@registry.test", "Registry Auditor")
		self.stranger = make_user("digest-stranger@registry.test", "Blogger")
		settings = frappe.get_single("Registry Digest")
		settings.enabled = 1
		settings.send_hour = 8
		settings.weekdays_only = 0
		settings.skip_if_nothing_new = 0
		settings.last_sent = None
		settings.last_status = None
		settings.set("recipients", [{"user": self.auditor}, {"user": self.stranger}])
		settings.save(ignore_permissions=True)
		# no outgoing mail server in tests: record the letters instead of queueing them
		self.sent = []
		patcher = patch.object(digest.frappe, "sendmail", side_effect=lambda **kw: self.sent.append(kw))
		patcher.start()
		self.addCleanup(patcher.stop)

	def working_with_account(self):
		"""A working employee with an enabled AD account: dismissing him makes a new alert."""
		return frappe.db.sql(
			"""select a.person from `tabAD Account` a join `tabPerson` p on p.name = a.person
			where a.enabled = 1 and a.missing_in_source = 0 and p.status = 'Работает' limit 1"""
		)[0][0]

	def emails(self):
		return self.sent

	def test_first_digest_then_only_new(self):
		log = digest.run()
		self.assertEqual(log.status, "Отправлено", log.error)
		self.assertIn("открытых замечаний", log.subject)
		self.assertEqual(log.recipients, self.auditor)  # the user without a registry role gets nothing
		self.assertTrue(log.open_alerts)
		self.assertEqual(log.new_alerts, 0)
		keys = json.loads(log.alert_keys)
		self.assertTrue(keys["dismissed"])
		self.assertEqual(len(self.emails()), 1)
		self.assertEqual(self.emails()[0]["recipients"], [self.auditor])

		# a new alert: one more working employee is dismissed while still having accounts
		person = self.working_with_account()
		frappe.db.set_value("Person", person, "status", "Уволен")
		full_name = frappe.db.get_value("Person", person, "full_name")
		frappe.cache().delete_value(api.CACHE_KEY)
		log = digest.run()
		self.assertGreaterEqual(log.new_alerts, 1)
		self.assertIn("новых замечаний", log.subject)
		self.assertIn(full_name, log.message_html)
		self.assertIn("/registry#/control/dismissed", log.message_html)

		# back to work: the alert is resolved, nothing new
		frappe.db.set_value("Person", person, "status", "Работает")
		log = digest.run()
		self.assertEqual(log.new_alerts, 0)
		self.assertGreaterEqual(log.resolved_alerts, 1)
		self.assertIn("нового нет", log.subject)

	def test_suppressed_alerts_are_not_new(self):
		digest.run()
		person = self.working_with_account()
		frappe.db.set_value("Person", person, "status", "Уволен")
		rows = [r for r in api.control("dismissed")["rows"] if r["person"] == person]
		self.assertTrue(rows)
		api.suppress_alerts("dismissed", [r["alert_key"] for r in rows], "учётки отключаются вручную")
		log = digest.run()
		self.assertNotIn(frappe.db.get_value("Person", person, "full_name"), log.message_html)

	def test_skip_if_nothing_new_and_preview(self):
		frappe.db.set_single_value("Registry Digest", "skip_if_nothing_new", 1)
		self.assertEqual(digest.run().status, "Отправлено")  # the first digest always goes
		self.sent.clear()
		with patch.object(digest, "source_problems", return_value=[]):
			log = digest.run()
		self.assertEqual(log.status, "Нового нет")
		self.assertEqual(self.emails(), [])
		preview = digest.run(preview=True)
		self.assertEqual(preview.status, "Предпросмотр")
		self.assertFalse(preview.alert_keys)
		self.assertEqual(digest.previous_log().name, log.name)  # a preview is not a baseline

	def test_no_recipients_is_an_error(self):
		frappe.get_doc("User", self.auditor).remove_roles("Registry Auditor")
		log = digest.run()
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("получателей", log.error)

	def test_other_sections(self):
		row = next(r for r in api.control("unlinked")["rows"])
		api.suppress_alerts("unlinked", [row["alert_key"]], "подрядчик", add_days(today(), 3))
		review = frappe.get_doc(
			{
				"doctype": "Access Review",
				"title": "Пересмотр Q3",
				"status": "Идёт",
				"due_date": add_days(today(), -2),
			}
		).insert(ignore_permissions=True)
		d = digest.build()
		self.assertTrue(any(row["account"] in e for e in d["expiring"]), d["expiring"])
		self.assertTrue(any(review.title in r for r in d["reviews"]))
		# sources: the portal of the fixture never loaded with the scheduler — a stale source is reported
		frappe.db.set_value(
			"B24 Portal",
			frappe.get_all("B24 Portal", pluck="name")[0],
			"last_sync",
			add_days(now_datetime(), -3),
		)
		self.assertTrue(any("Битрикс24" in s for s in digest.source_problems()))

	def test_schedule(self):
		at_eight = get_datetime(f"{today()} 08:05:00")
		with patch.object(digest, "now_datetime", return_value=at_eight), patch.object(digest, "run") as run:
			digest.scheduled()
			run.assert_called_once()
		with (
			patch.object(digest, "now_datetime", return_value=get_datetime(f"{today()} 09:05:00")),
			patch.object(digest, "run") as run,
		):
			digest.scheduled()
			run.assert_not_called()
		frappe.db.set_single_value(
			"Registry Digest", {"last_sent": at_eight, "last_status": "Отправлено (x)"}
		)
		with patch.object(digest, "now_datetime", return_value=at_eight), patch.object(digest, "run") as run:
			digest.scheduled()
			run.assert_not_called()  # once a day
		frappe.db.set_single_value("Registry Digest", {"enabled": 0, "last_sent": None})
		with patch.object(digest, "now_datetime", return_value=at_eight), patch.object(digest, "run") as run:
			digest.scheduled()
			run.assert_not_called()

	def test_no_mail_server_is_explained(self):
		with patch.object(digest.frappe, "sendmail", side_effect=frappe.OutgoingEmailError("no account")):
			log = digest.run()
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("Email Account", log.error)
		self.assertIn("Ошибка", frappe.db.get_single_value("Registry Digest", "last_status"))
