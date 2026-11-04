"""Tests of /registry, part 1: «Обзор» counters, «Контроль» lists and suppression rules,
the «Сотрудники» list and the person card (visibility of personal data).

The fixture (RegistryFixture) loads ZUP, the 1C rights snapshot, AD and Bitrix24: 8 people,
of them Иванов and Петрова have accounts in every system, Фёдоров (dismissed) and Новиков
(dismissed) still have enabled accounts, Кузнецова has two employments, Андреев has no
accounts at all.
"""

import frappe
from frappe.utils import add_days, now_datetime, today

from access_registry.access_roles import engine, suppression
from access_registry.registry import api
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_registry_app import RegistryFixture

FEDOROV = "Фёдоров Фёдор Фёдорович"
NOVIKOV = "Новиков Дмитрий Олегович"


class TestDashboardExtras(RegistryFixture):
	"""Counters of «Обзор» that test_registry_app does not touch."""

	def test_assets_are_none_without_snipe_it(self):
		self.assertIsNone(api.dashboard(refresh=1)["assets"])

	def test_expiring_counter_counts_exceptions_and_assignments(self):
		before = api.dashboard(refresh=1)["expiring"]
		api.create_exception(self.person(1), self.kadr, "Замещение на время отпуска", add_days(today(), 5))
		frappe.get_doc(
			{
				"doctype": "Access Role Assignment",
				"person": self.person(3),
				"access_role": "Кадровик",
				"valid_to": add_days(today(), 7),
				"reason": "временное поручение",
				"approved_by": "Administrator",
			}
		).insert(ignore_permissions=True)
		self.assertEqual(api.dashboard(refresh=1)["expiring"], before + 2)
		# far in the future — outside the 14-day window
		api.create_exception(self.person(1), self.kadr, "Долгосрочное", add_days(today(), 40))
		self.assertEqual(api.dashboard(refresh=1)["expiring"], before + 2)

	def test_quality_counters(self):
		self.assertEqual(frappe.db.get_value("IB User", {"person": self.person(1)}, "has_extra_roles"), 1)
		quality = api.dashboard(refresh=1)["quality"]
		# the same filters the dashboard uses: the API counts what is really there
		extra = frappe.db.count(
			"IB User", {"has_extra_roles": 1, "login_allowed": 1, "invalid": 0, "missing_in_source": 0}
		)
		admins = frappe.db.count("B24 User", {"is_admin": 1, "active": 1, "missing_in_source": 0})
		self.assertEqual(quality["extra_roles"], extra)  # Иванов and the orphan robot
		self.assertEqual(quality["b24_admins"], admins)  # Иванов is in the admins group of the fixture
		self.assertEqual(quality["merge_candidates"], 0)
		frappe.db.set_value("B24 User", {"person": self.person(1)}, "is_admin", 0)
		self.assertEqual(api.dashboard(refresh=1)["quality"]["b24_admins"], admins - 1)

	def test_sources_and_their_states(self):
		rows = api.sources()
		self.assertEqual(
			{(r["doctype"], r["kind"]) for r in rows},
			{
				("Info Base", "Кадры ЗУП"),
				("AD Domain", "Active Directory"),
				("B24 Portal", "Битрикс24"),
			},
		)
		for row in rows:
			self.assertEqual(row["state"], "ok", row)
			self.assertTrue(row["last"])
		base = next(r for r in rows if r["doctype"] == "Info Base")
		frappe.db.set_value("Info Base", base["name"], "last_status", "Ошибка: таймаут")
		self.assertEqual(next(r for r in api.sources() if r["name"] == base["name"])["state"], "error")
		frappe.db.set_value("Info Base", base["name"], "last_status", "Остановлен вручную")
		self.assertEqual(next(r for r in api.sources() if r["name"] == base["name"])["state"], "warn")
		frappe.db.set_value("Info Base", base["name"], "last_status", None)
		row = next(r for r in api.sources() if r["name"] == base["name"])
		# last_status is gone but last_sync (the «last» column) stays
		self.assertEqual((row["state"], row["status"]), ("none", None))
		self.assertTrue(row["last"])

	def test_reviewer_without_sections_sees_no_counters(self):
		make_user("reviewer-ov@registry.test", "Access Reviewer")
		frappe.set_user("reviewer-ov@registry.test")
		boot = api.bootstrap()
		self.assertFalse(boot["can"]["read"])
		self.assertTrue(boot["can"]["reviewer"])
		self.assertRaises(frappe.PermissionError, api.dashboard)
		self.assertRaises(frappe.PermissionError, api.people)
		self.assertRaises(frappe.PermissionError, api.control, "dismissed")


class TestPeopleList(RegistryFixture):
	"""The «Сотрудники» list: filters, flags and pagination."""

	def all_names(self, **kwargs):
		result = api.people(**kwargs)
		return result, {r["full_name"] for r in result["rows"]}

	def test_status_and_pagination(self):
		result, names = self.all_names(status="Работает")
		self.assertEqual(result["total"], 6)
		self.assertTrue(all(r["status"] == "Работает" for r in result["rows"]))
		page, names = self.all_names(limit=3)
		self.assertEqual((page["total"], len(page["rows"])), (8, 3))
		tail, names = self.all_names(start=6, limit=10)
		self.assertEqual(len(tail["rows"]), 2)
		self.assertEqual(names, {"Сидоров Пётр Алексеевич", FEDOROV})  # the last two by full name
		self.assertEqual(api.people(query="   ")["total"], 8)  # blank query means «everyone»

	def test_organization_filter(self):
		org = frappe.db.get_value("HR Organization", {"title": "Ромашка Сервис ООО"}, "name")
		result, names = self.all_names(organization=org)
		self.assertEqual(result["total"], 4)
		self.assertEqual(
			names,
			{"Андреев Олег Игоревич", "Орлова Елена Павловна", NOVIKOV, FEDOROV},
		)
		empty, names = self.all_names(organization="HR-ORG-NOPE")
		self.assertEqual((empty["total"], names), (0, set()))

	def test_flags(self):
		_, no_access = self.all_names(flag="no_access")
		# no active accounts anywhere: Андреев has none, Кузнецова's Bitrix24 user is inactive
		self.assertEqual(no_access, {"Андреев Олег Игоревич", "Кузнецова Анна Викторовна"})
		_, extra = self.all_names(flag="extra_roles")
		self.assertEqual(extra, {"Иванов Иван Иванович"})
		self.assertEqual(api.people(flag="nope")["total"], 0)
		# the flags are also present on plain rows
		row = next(r for r in api.people()["rows"] if r["full_name"] == "Иванов Иван Иванович")
		self.assertIn("extra_roles", row["flags"])

	def test_b24_admin_flag(self):
		# Иванов is in the «Администраторы» group of the fixture portal
		_, names = self.all_names(flag="b24_admin")
		self.assertEqual(names, {"Иванов Иван Иванович"})
		frappe.db.set_value("B24 User", {"person": self.person(1)}, "is_admin", 0)
		self.assertEqual(api.people(flag="b24_admin")["total"], 0)
		frappe.db.set_value("B24 User", {"person": self.person(1)}, "is_admin", 1)
		result, names = self.all_names(flag="b24_admin")
		self.assertEqual(names, {"Иванов Иван Иванович"})
		row = result["rows"][0]
		self.assertEqual(row["b24"], 1)


class TestPersonCard(RegistryFixture):
	"""The person card: structure, empty states and personal data visibility."""

	def test_working_person_with_everything(self):
		card = api.person(self.person(2))
		self.assertEqual(card["person"]["full_name"], "Петрова Мария Сергеевна")
		self.assertEqual(card["person"]["position"], "Главный бухгалтер")
		self.assertFalse(card["personal_hidden"])
		self.assertEqual(len(card["employments"]), 1)
		self.assertEqual(card["employments"][0]["employment_kind"], "Основное место работы")
		self.assertEqual([a.base_code for a in card["ib"]], ["TST1"])
		self.assertEqual([a.sam_account_name for a in card["ad"]], ["petrova"])
		self.assertTrue(card["b24"][0].workgroups)  # «Проект Внедрение» and «Бухгалтерия»
		self.assertTrue(card["b24_access"])
		self.assertEqual(card["shares"], [])
		self.assertEqual(card["assets"], [])
		self.assertIsNone(card["tickets"])  # no smart process of the helpdesk is connected
		self.assertIn("Главный бухгалтер", {r["role"] for r in card["roles"]})

	def test_part_time_employments(self):
		card = api.person(self.person(4))
		kinds = {e["employment_kind"] for e in card["employments"]}
		self.assertEqual(kinds, {"Основное место работы", "Внутреннее совместительство"})
		self.assertEqual(len(card["employments"]), 2)

	def test_person_without_accounts(self):
		card = api.person(self.person(5))  # Андреев, внешний совместитель
		self.assertEqual((card["ib"], card["ad"], card["b24"]), ([], [], []))
		self.assertEqual(card["b24_access"], [])
		self.assertIn("Все сотрудники", {r["role"] for r in card["roles"]})
		missing = {r["title"] for r in card["reconciliation"] if r["status"] == engine.MISSING}
		self.assertIn("AD: пользователи ЗУП", missing)

	def test_unknown_person_and_no_permissions(self):
		self.assertRaises(frappe.DoesNotExistError, api.person, "PS-NOPE-1")
		outsider = make_user("outsider-card@registry.test", "Blogger")
		frappe.set_user(outsider)
		self.assertRaises(frappe.PermissionError, api.person, self.person(2))

	def test_personal_data_is_hidden_from_viewers(self):
		frappe.get_doc(
			{
				"doctype": "HR Event",
				"event_type": "Уход в отпуск по уходу",
				"person": self.person(6),
				"event_date": "2026-06-01",
				"details": "подробности личного характера",
				"source": "TST1",
			}
		).insert(ignore_permissions=True)
		admin_card = api.person(self.person(6))
		self.assertTrue(admin_card["absences"])  # отпуск по уходу до 2027-09-01
		self.assertEqual(admin_card["events"][0]["details"], "подробности личного характера")
		viewer = make_user("viewer-card@registry.test", "Access Catalog Viewer")
		frappe.set_user(viewer)
		card = api.person(self.person(6))
		self.assertTrue(card["personal_hidden"])
		self.assertIsNone(card["person"]["birth_date"])
		self.assertEqual(card["absences"], [])
		for event in card["events"]:
			self.assertIsNone(event["details"])


class TestControlLists(RegistryFixture):
	"""Every «Контроль» list: rows, columns and the non-suppressible ones."""

	def assert_columns_match_rows(self, result):
		for row in result["rows"]:
			for column in result["columns"]:
				self.assertIn(column["key"], row, (result["kind"], column["key"]))

	def test_structure_of_every_list(self):
		for kind in api.CONTROLS:
			result = api.control(kind)
			self.assertEqual(
				(result["kind"], result["title"], result["suppressible"]),
				(kind, api.CONTROLS[kind], kind in suppression.SUPPRESSIBLE),
			)
			self.assertEqual(result["total"], len(result["rows"]))
			self.assert_columns_match_rows(result)
		# without Snipe-IT and file shares the two lists are empty but open
		self.assertEqual(api.control("shares")["rows"], [])
		self.assertEqual(api.control("assets")["rows"], [])
		# nothing is suppressed in the fixture, the journal is empty and cannot be suppressed
		self.assertEqual(api.control("journal")["rows"], [])
		self.assertFalse(api.control("journal")["suppressible"])

	def test_dismissed_formats(self):
		rows = api.control("dismissed")["rows"]
		fedorov = [r for r in rows if r["full_name"] == FEDOROV]
		self.assertEqual({r["system"] for r in fedorov}, {"1С", "AD", "Битрикс24"})
		ad = next(r for r in fedorov if r["system"] == "AD")
		self.assertTrue(ad["account"].startswith("TSTAD\\"), ad["account"])
		self.assertTrue(ad["account"].endswith("\\fedorov"), ad["account"])
		self.assertIsNotNone(ad["last_activity"])
		ib = next(r for r in fedorov if r["system"] == "1С")
		self.assertTrue(ib["account"].startswith("TST1: "), ib["account"])
		# a missing_in_source account does not count as access: none of the rows is marked so
		self.assertFalse([r for r in rows if r["person"] == ""])

	def test_unlinked_who_is_listed(self):
		rows = api.control("unlinked")["rows"]
		by_system = {}
		for row in rows:
			by_system.setdefault(row["system"], set()).add(row["account"])
		# «Администратор» (1С), the backup and Орлова (AD, she is linked only by employeeNumber
		# in another test) and Смирнов (Битрикс24)
		self.assertIn("TST1: Администратор", by_system.get("1С", set()))
		self.assertEqual(by_system.get("AD", set()), {"TSTAD: svc_backup", "TSTAD: Орлова Е. П."})
		self.assertIn("TSTB24: Смирнов Олег", by_system.get("Битрикс24", set()))
		accounts = {row["account"] for row in rows}
		# the extranet partner, the orphan robot and the invalid 1С card are out of the list
		self.assertFalse([a for a in accounts if "partner@partner.example" in a])
		self.assertFalse([a for a in accounts if "robot_exchange" in a])
		self.assertFalse([a for a in accounts if "Карточка без пользователя" in a])
		for row in rows:
			self.assertTrue(row["note"] is None or isinstance(row["note"], str))

	def test_stale_accounts(self):
		rows = api.control("stale")["rows"]
		# Новиков never logged in (last_logon is null) — stale no matter when the test runs
		novikov = next(r for r in rows if r["full_name"] == NOVIKOV)
		self.assertEqual((novikov["system"], novikov["last_activity"]), ("AD", None))
		# a fresh logon removes the alert, an old one brings it back; Иванов is counted in AD
		# and in Битрикс24, so both last_logon and last_login matter
		account = frappe.db.get_value("AD Account", {"person": self.person(1), "enabled": 1}, "name")
		b24 = frappe.db.get_value("B24 User", {"person": self.person(1), "active": 1}, "name")
		frappe.db.set_value("AD Account", account, "last_logon", now_datetime())
		frappe.db.set_value("B24 User", b24, "last_login", now_datetime())
		self.assertNotIn("Иванов Иван Иванович", {r["full_name"] for r in api.control("stale")["rows"]})
		frappe.db.set_value("AD Account", account, "last_logon", add_days(now_datetime(), -120))
		frappe.db.set_value("B24 User", b24, "last_login", add_days(now_datetime(), -120))
		ivanov = next(r for r in api.control("stale")["rows"] if r["full_name"] == "Иванов Иван Иванович")
		self.assertIsNotNone(ivanov["last_activity"])

	def test_exceptions_and_temporary_roles(self):
		api.create_exception(self.person(1), self.kadr, "Замещение", add_days(today(), 5))
		frappe.get_doc(
			{
				"doctype": "Access Role Assignment",
				"person": self.person(3),
				"access_role": "Кадровик",
				"reason": "временное поручение",
				"approved_by": "Administrator",
			}
		).insert(ignore_permissions=True)
		result = api.control("exceptions")
		self.assert_columns_match_rows(result)
		kinds = {(r["full_name"], r["kind"], r["title"]) for r in result["rows"]}
		self.assertIn(("Иванов Иван Иванович", "Исключение", "1С: Кадровик"), kinds)
		self.assertIn(("Сидоров Пётр Алексеевич", "Роль вручную", "Кадровик"), kinds)
		ivanov = next(r for r in result["rows"] if r["kind"] == "Исключение")
		sidorov = next(r for r in result["rows"] if r["kind"] == "Роль вручную")
		self.assertTrue(ivanov["valid_to"])  # dated first, open-ended last
		self.assertIsNone(sidorov["valid_to"])
		self.assertEqual(sidorov["approved_by"], "Administrator")

	def test_privileged(self):
		rows = api.control("privileged")["rows"]
		extra = [r for r in rows if "в обход профилей" in r["title"]]
		# Иванов and the orphan robot: «ПолныеПрава» outside any supplied profile
		self.assertEqual({r["full_name"] for r in extra}, {"Иванов Иван Иванович", "(не привязан)"})
		ivanov_extra = next(r for r in extra if r["person"])
		self.assertEqual((ivanov_extra["system"], ivanov_extra["risk"]), ("1С", "Высокий"))
		# Иванов is in the admins group of the fixture portal — the alert is already there
		admins = [r for r in rows if "администратор портала" in r["title"]]
		self.assertEqual([r["full_name"] for r in admins], ["Иванов Иван Иванович"])
		frappe.db.set_value("B24 User", {"person": self.person(2)}, "is_admin", 1)
		rows = api.control("privileged")["rows"]
		admins = [r for r in rows if "администратор портала" in r["title"]]
		self.assertEqual(
			{r["full_name"] for r in admins}, {"Иванов Иван Иванович", "Петрова Мария Сергеевна"}
		)
		for admin in admins:
			self.assertEqual((admin["risk"], admin["system"]), ("Критичный", "Битрикс24"))
		# «Критичный» is sorted before «Высокий»
		critical = [i for i, r in enumerate(rows) if r["risk"] == "Критичный"]
		high = [i for i, r in enumerate(rows) if r["risk"] == "Высокий"]
		self.assertLess(max(critical), min(high))

	def test_quality(self):
		rows = api.control("quality")["rows"]
		ad_rows = [r for r in rows if r["area"] == "AD: employeeNumber"]
		# Орлова is linked only by employeeNumber in another test — here her account is unlinked
		self.assertEqual(
			{r["full_name"] for r in ad_rows},
			{FEDOROV, NOVIKOV, "Иванов Иван Иванович", "Петрова Мария Сергеевна"},
		)
		for row in ad_rows:
			self.assertEqual(row["current"], "—")
			self.assertEqual(row["expected"], row["person"])

	def test_processes(self):
		result = api.control("processes")
		self.assert_columns_match_rows(result)
		row = next(r for r in result["rows"] if r["process_title"] == "Закрытие месяца")
		self.assertIn("нет участников", row["problems"])
		self.assertEqual(row["available"], 0)

	def test_these_lists_do_not_suppress(self):
		for kind in ("events", "excess", "missing", "exceptions", "journal"):
			self.assertRaises(
				frappe.ValidationError, api.suppress_alerts, kind, ["x"], "замечание согласовано"
			)


class TestControlEvents(RegistryFixture):
	"""«Кадровые события: что сделать» — every kind of recommendation."""

	def event(self, n, kind, **kwargs):
		frappe.get_doc(
			{
				"doctype": "HR Event",
				"event_type": kind,
				"person": self.person(n),
				"event_date": "2026-06-01",
				"source": "TST1",
				**kwargs,
			}
		).insert(ignore_permissions=True)

	def todo(self, n):
		rows = api.control("events")["rows"]
		return next(r["todo"] for r in rows if r["person"] == self.person(n))

	def test_hire_without_accounts(self):
		self.event(5, "Приём")  # Андреев, accounts do not exist yet
		self.assertEqual(self.todo(5), "Выдать: AD: пользователи ЗУП; Учёток ещё нет: завести")

	def test_transfer_revokes_excess(self):
		self.event(1, "Перевод")  # Иванов holds the «Кадровик» profile without being entitled
		self.assertEqual(self.todo(1), "Отозвать лишнее после перевода: 1С: Кадровик")

	def test_planned_dismissal_schedules_disable(self):
		employment = frappe.db.get_value("Employment", {"person": self.person(1)}, "name")
		self.event(1, "Предстоящее увольнение", employment=employment)
		self.assertEqual(self.todo(1), "Запланировать отключение в последний рабочий день: 1С, AD, Битрикс24")

	def test_event_without_employment_keeps_accounts(self):
		# it is unknown which employment ends — the safe recommendation is to keep the accounts
		self.event(1, "Предстоящее увольнение")
		self.assertIn("Учётки не отключать", self.todo(1))

	def test_dismissal_with_another_employment_keeps_accounts(self):
		# Кузнецова works both as «Начальник отдела продаж» and part-time «Бухгалтер»
		part_time = frappe.get_all(
			"Employment",
			filters={"person": self.person(4), "employment_kind": "Внутреннее совместительство"},
			pluck="name",
		)[0]
		self.event(4, "Увольнение", employment=part_time)
		todo = self.todo(4)
		self.assertIn("Учётки не отключать", todo)
		self.assertIn("Начальник отдела продаж", todo)
		self.assertNotIn("Отключить учётки", todo)


class TestSuppressionRules(RegistryFixture):
	"""Who may suppress and restore: not one's own alerts, and only visible systems."""

	def dismissed_keys(self, person):
		rows = api.control("dismissed")["rows"]
		return [r["alert_key"] for r in rows if r["person"] == person]

	def test_cannot_suppress_one_own_alert(self):
		auditor = make_user("fedorov@example.com", "Registry Auditor")
		fedorov = self.person(8)
		novikov = self.person(7)
		frappe.set_user(auditor)
		# the AD mail of Фёдоров is fedorov@example.com — the auditor is Фёдоров
		own = self.dismissed_keys(fedorov)
		self.assertTrue(own)
		self.assertRaises(frappe.PermissionError, api.suppress_alerts, "dismissed", own, "по ошибке")
		self.assertEqual(api.suppress_alerts("dismissed", self.dismissed_keys(novikov), "уволен"), 1)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Alert Suppression", {}, "suppressed_by"), auditor)

	def test_restore_needs_the_system(self):
		fedorov = self.person(8)
		frappe.set_user("Administrator")
		api.suppress_alerts("dismissed", self.dismissed_keys(fedorov), "учётки ещё нужны")
		entries = {
			e.ref_doctype: e.name for e in frappe.get_all("Alert Suppression", fields=["name", "ref_doctype"])
		}
		limit_to_1c = make_user("control-1c@registry.test")
		api.save_profile(
			{
				"profile_name": "Контроль 1С",
				"sections": {"control": 2},
				"systems": ["1c"],
				"members": [limit_to_1c],
			}
		)
		frappe.set_user(limit_to_1c)
		self.assertRaises(frappe.PermissionError, api.restore_alert, entries["AD Account"], "не трогать")
		api.restore_alert(entries["IB User"], "учётку отключили")
		self.assertIn(
			self.dismissed_keys(fedorov)[0], [r["alert_key"] for r in api.control("dismissed")["rows"]]
		)
