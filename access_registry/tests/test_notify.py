"""Notification rules: only the new, only what the recipient may see, channels, schedule, the bell."""

from datetime import datetime

import frappe
from frappe.utils import add_days, today

from access_registry import notify
from access_registry.registry import api
from access_registry.tests.test_it_assets import SnipeFixture
from access_registry.tests.test_permissions import make_user


class TestNotify(SnipeFixture):
	def setUp(self):
		super().setUp()
		for doctype in ("Registry Notice", "Registry Notification Rule", "AD Change Plan"):
			frappe.db.delete(doctype)
		frappe.db.delete("Email Queue")
		frappe.db.delete("Registry Access Member", {"parent": ["like", "% (уведомления)"]})
		frappe.db.delete("Registry Access Profile", {"name": ["like", "% (уведомления)"]})
		self.people = make_user("notify-people@registry.test")
		self.nobody = make_user("notify-nobody@registry.test")
		api.save_profile(
			{"profile_name": "Кадры (уведомления)", "sections": {"people": 1}, "members": [self.people]}
		)
		api.save_profile(
			{"profile_name": "Техника (уведомления)", "sections": {"equipment": 1}, "members": [self.nobody]}
		)

	def rule(self, event, **kw):
		data = {
			"title": f"Тест: {event}",
			"event": event,
			"channel_email": 1,
			"channel_app": 1,
			"users": [self.people, self.nobody],
			**kw,
		}
		name = api.save_notification_rule(data)
		return frappe.get_doc("Registry Notification Rule", name)

	def hr_event(self):
		person = frappe.get_all("Person", pluck="name", limit=1)[0]
		return frappe.get_doc(
			{"doctype": "HR Event", "event_type": "Приём", "person": person, "event_date": today()}
		).insert(ignore_permissions=True)

	def notices(self, user):
		return frappe.get_all("Registry Notice", filters={"for_user": user}, pluck="title")

	def test_first_run_remembers_then_only_new_and_only_visible(self):
		rule = self.rule(notify.HR)
		self.hr_event()
		self.assertEqual(notify.run(rule), {})  # the first run only remembers
		self.assertEqual(self.notices(self.people), [])

		rule.reload()
		event = self.hr_event()
		sent = notify.run(rule)
		self.assertEqual(list(sent), [self.people])  # the other recipient has no «Сотрудники»
		self.assertEqual(len(self.notices(self.people)), 1)
		self.assertEqual(self.notices(self.nobody), [])
		if frappe.db.exists("Email Account", {"default_outgoing": 1}):
			self.assertTrue(frappe.db.exists("Email Queue", {"message": ["like", f"%{event.person}%"]}))
		else:  # no outgoing mail on the test site: the bell still works and the rule says why
			self.assertIn(
				"почта не отправлена",
				frappe.db.get_value("Registry Notification Rule", rule.name, "last_status"),
			)

		rule.reload()
		self.assertEqual(notify.run(rule), {})  # already sent: not again

	def test_profiles_and_channels(self):
		rule = self.rule(notify.HR, users=[], profiles=["Кадры (уведомления)"], channel_email=0)
		self.assertEqual(notify.recipients(rule), [self.people])
		notify.run(rule)
		rule.reload()
		self.hr_event()
		notify.run(rule)
		self.assertEqual(len(self.notices(self.people)), 1)
		self.assertFalse(frappe.db.count("Email Queue"))

	def test_ad_plan_decision_only_for_ib_not_author(self):
		ib = make_user("notify-ib@registry.test")
		it = make_user("notify-it@registry.test")
		api.save_profile(
			{"profile_name": "ИБ (уведомления)", "sections": {"control": 1}, "ad_approve": 1, "members": [ib]}
		)
		api.save_profile({"profile_name": "ИТ (уведомления)", "sections": {"control": 2}, "members": [it]})
		rule = self.rule(notify.AD_PLAN, users=[ib, it])
		notify.run(rule)
		rule.reload()
		frappe.set_user(it)
		plan = frappe.get_doc({"doctype": "AD Change Plan", "domain": "TSTAD", "status": "Черновик"}).insert(
			ignore_permissions=True
		)
		frappe.set_user("Administrator")
		sent = notify.run(rule)
		self.assertEqual(list(sent), [ib])  # the author does not approve their own plan
		self.assertIn(plan.name, sent[ib][0].text)

	def test_schedule(self):
		rule = self.rule(notify.HR, frequency="Раз в день", send_hour=9, weekdays_only=1)
		monday_9 = datetime(2026, 10, 5, 9, 10)
		self.assertTrue(notify.due(rule, monday_9))
		self.assertFalse(notify.due(rule, datetime(2026, 10, 5, 10, 10)))
		self.assertFalse(notify.due(rule, datetime(2026, 10, 4, 9, 10)))  # Sunday
		rule.last_run = datetime(2026, 10, 5, 9, 3)
		self.assertFalse(notify.due(rule, monday_9))  # once a day
		rule.enabled = 0
		self.assertFalse(notify.due(rule, datetime(2026, 10, 6, 9, 10)))

	def test_bell_and_rights(self):
		frappe.get_doc({"doctype": "Registry Notice", "for_user": self.people, "title": "Моё"}).insert(
			ignore_permissions=True
		)
		other = frappe.get_doc(
			{"doctype": "Registry Notice", "for_user": self.nobody, "title": "Чужое"}
		).insert(ignore_permissions=True)
		frappe.set_user(self.people)
		d = api.notices()
		self.assertEqual([n.title for n in d["notices"]], ["Моё"])
		self.assertEqual(d["unread"], 1)
		self.assertEqual(api.mark_notices_read([other.name]), 0)  # someone else's: untouched
		self.assertEqual(api.mark_notices_read(), 1)
		self.assertEqual(api.notices()["unread"], 0)
		self.assertRaises(frappe.PermissionError, api.notification_rules)
		self.assertRaises(
			frappe.PermissionError, api.save_notification_rule, {"title": "x", "event": notify.HR}
		)

	def test_preview_and_validation(self):
		rule = self.rule(notify.HR)
		self.hr_event()
		preview = {r["user"]: r["count"] for r in api.notification_preview(rule.name)}
		self.assertGreater(preview[self.people], 0)
		self.assertEqual(preview[self.nobody], 0)
		self.assertRaises(
			frappe.ValidationError,
			api.save_notification_rule,
			{"title": "x", "event": notify.HR, "channel_email": 0, "channel_app": 0},
		)
		self.assertRaises(
			frappe.ValidationError, api.save_notification_rule, {"title": "x", "event": "что-то"}
		)
		# old notices are cleaned up
		old = frappe.get_doc(
			{"doctype": "Registry Notice", "for_user": self.people, "title": "старое"}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Registry Notice", old.name, "creation", add_days(today(), -200))
		notify.scheduled(commit=False)
		self.assertFalse(frappe.db.exists("Registry Notice", old.name))
