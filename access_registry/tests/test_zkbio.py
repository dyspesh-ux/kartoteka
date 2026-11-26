"""Tests of the ZKBio (СКУД) module: scaffold, client config, persons/events sync, reports."""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestZKBioScaffold(FrappeTestCase):
	def test_module_scaffold(self):
		for doctype in ("ZKBio Server", "ZKBio Person", "ZKBio Event"):
			self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
		kind = frappe.get_meta("Sync Log").get_field("kind")
		self.assertIn("СКУД", kind.options.split("\n"))
		ws = frappe.get_doc("Workspace", "СКУД")
		linked = {row.link_to for row in ws.links}
		self.assertTrue({"ZKBio Server", "ZKBio Person", "ZKBio Event"} <= linked)


class TestPassNos(FrappeTestCase):
	def test_pass_nos_parsing(self):
		from access_registry.zkbio.sync import pass_nos

		server = frappe._dict(pass_event_nos=None)
		self.assertEqual(pass_nos(server), {0})
		server.pass_event_nos = " 0, 202 ,208 "
		self.assertEqual(pass_nos(server), {0, 202, 208})
		server.pass_event_nos = "0,abc, 14x"
		self.assertEqual(pass_nos(server), {0})  # «abc»/«14x» проигнорированы

	def test_missing_driver_message(self):
		import sys
		from unittest import mock

		from access_registry.zkbio import pg_client

		with mock.patch.dict(sys.modules, {"psycopg2": None}):
			with self.assertRaises(frappe.ValidationError) as ctx:
				pg_client._psycopg2()
		self.assertIn("psycopg2-binary", str(ctx.exception))


def make_server(code="TSKD1"):
	if not frappe.db.exists("ZKBio Server", code):
		return frappe.get_doc(
			{
				"doctype": "ZKBio Server",
				"server_code": code,
				"host": "127.0.0.1",
				"username": "ro",
				"password": "x",
			}
		).insert(ignore_permissions=True)
	return frappe.get_doc("ZKBio Server", code)


def person_row(zid, last, first, pin="100", dept="Ит", status=0):
	return {
		"id": zid,
		"pin": pin,
		"last_name": last,
		"first_name": first,
		"email": "",
		"dept_name": dept,
		"hire_date": None,
		"status": status,
		"update_time": None,
	}


class TestZKBioPersonsSync(FrappeTestCase):
	def test_create_update_missing(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server()
		log = run_server_sync(
			"TSKD1",
			"persons",
			commit=False,
			fetch=lambda _s: [person_row("p1", "Тестскуд", "Иван"), person_row("p2", "Тестскуд", "Пётр")],
		)
		self.assertEqual(log.status, "Успех")
		self.assertEqual(log.kind, "СКУД")
		self.assertEqual(frappe.db.count("ZKBio Person", {"server": "TSKD1"}), 2)
		# повтор без изменений: ни версий, ни обновлений
		versions = frappe.db.count("Version", {"ref_doctype": "ZKBio Person"})
		run_server_sync(
			"TSKD1",
			"persons",
			commit=False,
			fetch=lambda _s: [
				person_row("p1", "Тестскуд", "Иван"),
				person_row("p2", "Тестскуд", "Пётр", dept="Бухгалтерия"),
			],
		)
		# только p2 изменился
		self.assertEqual(frappe.db.count("Version", {"ref_doctype": "ZKBio Person"}), versions + 1)
		# p1 пропал из выгрузки
		run_server_sync(
			"TSKD1", "persons", commit=False, fetch=lambda _s: [person_row("p2", "Тестскуд", "Пётр")]
		)
		self.assertEqual(frappe.db.get_value("ZKBio Person", "TSKD1:p1", "missing_in_source"), 1)

	def test_guard_trips_on_shrink(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("TSKDG")
		rows = [person_row(f"g{i}", "Тестскудгард", f"Н{i}") for i in range(25)]  # > guard_min_records
		run_server_sync("TSKDG", "persons", commit=False, fetch=lambda _s: rows)
		log = run_server_sync("TSKDG", "persons", commit=False, fetch=lambda _s: rows[:2])
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertEqual(frappe.db.count("ZKBio Person", {"server": "TSKDG", "missing_in_source": 0}), 25)

	def test_no_names_in_log(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("TSKDL")
		log = run_server_sync(
			"TSKDL",
			"persons",
			commit=False,
			fetch=lambda _s: [person_row("x1", "Тестскудтайный", "Неизвестный")],
		)
		self.assertNotIn("Тестскудтайный", log.messages or "")

	def test_duplicate_pin_warns(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("TSKDDP")
		log = run_server_sync(
			"TSKDDP",
			"persons",
			commit=False,
			fetch=lambda _s: [
				person_row("dp1", "Тестскудпин1", "А", pin="555"),
				person_row("dp2", "Тестскудпин2", "Б", pin="555"),
			],
		)
		self.assertIn("555", log.messages)


class TestZKBioLinking(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.person = frappe.get_doc(
			{
				"doctype": "Person",
				"last_name": "Тестскудлинк",
				"first_name": "Лев",
				"middle_name": "Львович",
				"status": "Работает",
			}
		).insert(ignore_permissions=True)

	def test_link_by_fio(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("TSKDP")
		run_server_sync(
			"TSKDP", "persons", commit=False, fetch=lambda _s: [person_row("lp1", "Тестскудлинк", "Лев")]
		)
		row = frappe.db.get_value("ZKBio Person", "TSKDP:lp1", ["person", "person_link_method"], as_dict=True)
		self.assertEqual((row.person, row.person_link_method), (self.person.name, "ФИО"))

	def test_namesake_stays_unlinked(self):
		frappe.get_doc(
			{"doctype": "Person", "last_name": "Тестскуддубль", "first_name": "Иван", "status": "Работает"}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{"doctype": "Person", "last_name": "Тестскуддубль", "first_name": "Иван", "status": "Работает"}
		).insert(ignore_permissions=True)
		from access_registry.zkbio.sync import run_server_sync

		make_server("TSKDD")
		run_server_sync(
			"TSKDD", "persons", commit=False, fetch=lambda _s: [person_row("d1", "Тестскуддубль", "Иван")]
		)
		row = frappe.db.get_value("ZKBio Person", "TSKDD:d1", ["person", "person_link_note"], as_dict=True)
		self.assertFalse(row.person)
		self.assertIn("несколько", row.person_link_note)

	def test_manual_person_wins_and_survives(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("TSKDM")
		run_server_sync(
			"TSKDM", "persons", commit=False, fetch=lambda _s: [person_row("m1", "Тестскудручной", "Мария")]
		)
		doc = frappe.get_doc("ZKBio Person", "TSKDM:m1")
		doc.manual_person = self.person.name
		doc.save(ignore_permissions=True)
		self.assertEqual((doc.person, doc.person_link_method), (self.person.name, "Вручную"))
		# ФИО сменилось — ручная привязка всё равно главнее
		run_server_sync(
			"TSKDM", "persons", commit=False, fetch=lambda _s: [person_row("m1", "Тестскудлинк", "Лев")]
		)
		doc.reload()
		self.assertEqual(doc.person, self.person.name)

	def test_rename_relinks_unless_manual(self):
		from access_registry.zkbio.sync import run_server_sync

		other = frappe.get_doc(
			{"doctype": "Person", "last_name": "Тестскудновая", "first_name": "Анна", "status": "Работает"}
		).insert(ignore_permissions=True)
		make_server("TSKDR")
		run_server_sync(
			"TSKDR", "persons", commit=False, fetch=lambda _s: [person_row("r1", "Тестскудлинк", "Лев")]
		)
		run_server_sync(
			"TSKDR", "persons", commit=False, fetch=lambda _s: [person_row("r1", "Тестскудновая", "Анна")]
		)
		self.assertEqual(frappe.db.get_value("ZKBio Person", "TSKDR:r1", "person"), other.name)


def event_row(eid, dt, pin="100", event_no=0, point="Вход"):
	return {
		"id": eid,
		"event_time": dt,
		"pin": pin,
		"card_no": "123",
		"dev_alias": "Турникет",
		"event_point_name": point,
		"reader_name": "R1",
		"event_no": event_no,
		"event_name": f"acc_eventNo_{event_no}",
		"verify_mode_name": "Card",
		"acc_zone": "",
		"area_name": "",
	}


def fetch_with(rows):
	"""fetch(server, since, limit): события строго позже since, по возрастанию."""
	sorted_rows = sorted(rows, key=lambda r: r["event_time"])

	def _fetch(_server, since, limit):
		return [r for r in sorted_rows if r["event_time"] > since][:limit]

	return _fetch


class TestZKBioEventsSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.person = frappe.get_doc(
			{"doctype": "Person", "last_name": "Тестскудсобытие", "first_name": "Елена", "status": "Работает"}
		).insert(ignore_permissions=True)
		make_server("TSKDE")
		from access_registry.zkbio.sync import run_server_sync

		run_server_sync(
			"TSKDE",
			"persons",
			commit=False,
			fetch=lambda _s: [person_row("e1", "Тестскудсобытие", "Елена", pin="100")],
		)

	def test_first_run_starts_180_days_back(self):
		from access_registry.zkbio.sync import run_server_sync

		seen_since = []
		log = run_server_sync(
			"TSKDE", "events", commit=False, fetch=lambda s, since, limit: seen_since.append(since) or []
		)
		self.assertEqual(log.status, "Успех")
		# перекрытие (overlap) применяется только к существующему watermark
		self.assertEqual(seen_since[0].date(), frappe.utils.add_days(frappe.utils.getdate(), -180))

	def test_watermark_dedupe_and_filters(self):
		from datetime import datetime, timedelta

		from access_registry.zkbio.sync import run_server_sync

		now = datetime.now().replace(microsecond=0)
		rows = [
			event_row("t1", now - timedelta(hours=2), pin="100"),  # проход
			event_row("t2", now - timedelta(hours=1), pin="100", event_no=202),  # не в белом списке
			event_row("t3", now - timedelta(minutes=30), pin=""),  # без pin
			event_row("t4", now, pin="999"),  # чужой pin — сирота
		]
		log = run_server_sync("TSKDE", "events", commit=False, fetch=fetch_with(rows))
		self.assertEqual(log.status, "Успех")
		ev = frappe.db.get_value(
			"ZKBio Event", "TSKDE:t1", ["person", "zkbio_person", "event_point_name"], as_dict=True
		)
		self.assertEqual(ev.person, self.person.name)
		self.assertEqual(ev.zkbio_person, "TSKDE:e1")
		self.assertFalse(frappe.db.exists("ZKBio Event", "TSKDE:t2"))  # отфильтрован по коду
		self.assertFalse(frappe.db.exists("ZKBio Event", "TSKDE:t3"))  # без pin
		self.assertTrue(frappe.db.exists("ZKBio Event", "TSKDE:t4"))  # сирота хранится без person
		self.assertIsNone(frappe.db.get_value("ZKBio Event", "TSKDE:t4", "person") or None)
		# watermark на max(event_time); повтор с перекрытием не даёт дублей
		server = frappe.get_doc("ZKBio Server", "TSKDE")
		self.assertEqual(frappe.utils.get_datetime(server.events_watermark), now)
		import json as jsonlib

		log2 = run_server_sync("TSKDE", "events", commit=False, fetch=fetch_with(rows))
		stats = jsonlib.loads(log2.stats)
		self.assertEqual(stats["ZKBio Event"].get("created", 0), 0)
		self.assertGreaterEqual(stats["ZKBio Event"].get("duplicates", 0), 1)


class TestZKBioPermissions(FrappeTestCase):
	def test_buttons_require_admin(self):
		from access_registry.tests.test_permissions import make_user

		make_server("TSKDPR")
		nobody = make_user("zkbio-nobody@registry.test")
		doc = frappe.get_doc("ZKBio Server", "TSKDPR")
		frappe.set_user(nobody)
		try:
			with self.assertRaises(frappe.PermissionError):
				doc.sync_now()
			with self.assertRaises(frappe.PermissionError):
				doc.test_connection()
			with self.assertRaises(frappe.PermissionError):
				doc.refetch_events(7)
		finally:
			frappe.set_user("Administrator")

	def test_refetch_shifts_watermark(self):
		from frappe.utils import add_days, now_datetime

		make_server("TSKDSB")
		doc = frappe.get_doc("ZKBio Server", "TSKDSB")
		doc.db_set("events_watermark", now_datetime())
		doc.refetch_events(7)
		doc.reload()
		self.assertEqual(
			frappe.utils.get_datetime(doc.events_watermark).date(), add_days(frappe.utils.getdate(), -7)
		)


def make_hr_person(last, first, status="Работает"):
	return frappe.get_doc(
		{"doctype": "Person", "last_name": last, "first_name": first, "status": status}
	).insert(ignore_permissions=True)


class TestZKBioAttendanceReport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from access_registry.zkbio.sync import run_server_sync

		cls.day = frappe.utils.getdate()
		cls.present = make_hr_person("Тестскудотчёт", "Пётр")
		cls.vacationer = make_hr_person("Тестскудотчёт", "Вера")
		cls.dismissed = make_hr_person("Тестскудотчёт", "Уволен")
		make_server("TSKDO")
		run_server_sync(
			"TSKDO",
			"persons",
			commit=False,
			fetch=lambda _s: [
				person_row("o1", "Тестскудотчёт", "Пётр", pin="501"),
				person_row("o2", "Тестскудотчёт", "Вера", pin="502"),
				person_row("o3", "Тестскудотчёт", "Уволен", pin="503"),
			],
		)

		def dt(hour):
			return frappe.utils.get_datetime(f"{cls.day} {hour:02d}:00:00")

		events = [
			event_row("a1", dt(9), pin="501"),
			event_row("a2", dt(18), pin="501"),
			event_row("a3", dt(10), pin="502"),
			event_row("a4", dt(11), pin="503"),
		]
		run_server_sync("TSKDO", "events", commit=False, fetch=fetch_with(events))
		# после синка: Вера в отпуске на сегодня, Уволен — уволен
		if not frappe.db.exists("Info Base", "ZUP1"):
			frappe.get_doc(
				{
					"doctype": "Info Base",
					"source_code": "ZUP1",
					"title": "ZUP1",
					"base_url": "http://127.0.0.1:9/hs",
				}
			).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "HR Absence",
				"source": "ZUP1",
				"employment_guid": "g-test",
				"state_code": "ОТ",
				"state": "Отпуск ежегодный",
				"category": "Отпуск",
				"date_from": cls.day,
				"date_to": cls.day,
				"person": cls.vacationer.name,
				"record_key": f"ZUP1:g-test:ОТ:{cls.day}",
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Person", cls.dismissed.name, "status", "Уволен")

	def test_first_and_last(self):
		from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import execute

		_columns, rows = execute({"date_from": self.day, "date_to": self.day})
		mine = [r for r in rows if r["person"] == self.present.name]
		self.assertEqual(len(mine), 1)
		self.assertEqual(str(mine[0]["first_event"])[:5], "09:00")
		self.assertEqual(str(mine[0]["last_event"])[:5], "18:00")
		self.assertEqual(mine[0]["events_count"], 2)

	def test_absent_hidden_by_default_shown_with_flag(self):
		from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import execute

		filters = {"date_from": self.day, "date_to": self.day}
		_columns, rows = execute(filters)
		self.assertNotIn(self.vacationer.name, {r["person"] for r in rows})
		_columns, rows = execute({**filters, "show_absent": 1})
		mine = [r for r in rows if r["person"] == self.vacationer.name]
		self.assertEqual(mine[0]["absence"], "Отпуск ежегодный")

	def test_dismissed_never_shown(self):
		from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import execute

		for extra in ({}, {"show_absent": 1}):
			_columns, rows = execute({"date_from": self.day, "date_to": self.day, **extra})
			self.assertNotIn(self.dismissed.name, {r["person"] for r in rows})


class TestZKBioNoPassesReport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from frappe.utils import add_days, getdate

		from access_registry.zkbio.sync import run_server_sync

		cls.to = getdate()
		cls.from_ = add_days(cls.to, -1)
		cls.no_card = make_hr_person("Тестскудбезкарта", "Нина")
		cls.with_events = make_hr_person("Тестскудбезпроходов", "Семён")
		cls.on_leave = make_hr_person("Тестскудбезпроходов", "Ольга")
		make_server("TSKDB")
		run_server_sync(
			"TSKDB",
			"persons",
			commit=False,
			fetch=lambda _s: [
				person_row("b1", "Тестскудбезпроходов", "Семён", pin="601"),
				person_row("b2", "Тестскудбезпроходов", "Ольга", pin="602"),
			],
		)
		yesterday = frappe.utils.get_datetime(f"{cls.from_} 10:00:00")
		run_server_sync(
			"TSKDB", "events", commit=False, fetch=fetch_with([event_row("b1e", yesterday, pin="601")])
		)
		if not frappe.db.exists("Info Base", "ZUP1"):
			frappe.get_doc(
				{
					"doctype": "Info Base",
					"source_code": "ZUP1",
					"title": "ZUP1",
					"base_url": "http://127.0.0.1:9/hs",
				}
			).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "HR Absence",
				"source": "ZUP1",
				"employment_guid": "g-test-2",
				"state_code": "ОТ",
				"state": "Отпуск ежегодный",
				"category": "Отпуск",
				"date_from": cls.from_,
				"date_to": cls.to,
				"person": cls.on_leave.name,
				"record_key": f"ZUP1:g-test-2:ОТ:{cls.from_}",
			}
		).insert(ignore_permissions=True)

	def test_shows_only_workers_without_events(self):
		from access_registry.zkbio.report.zkbio_no_passes.zkbio_no_passes import execute

		_columns, rows = execute({"date_from": self.from_, "date_to": self.to})
		persons = {r["person"] for r in rows}
		self.assertIn(self.no_card.name, persons)  # работает, событий нет, карты нет
		self.assertNotIn(self.with_events.name, persons)  # события были
		self.assertNotIn(self.on_leave.name, persons)  # отпуск на весь период
		mine = [r for r in rows if r["person"] == self.no_card.name][0]
		self.assertEqual(mine["has_card"], 0)
		self.assertEqual(mine["absence_days"], 0)


MON = "2026-11-23"  # понедельник
SAT = "2026-11-28"  # суббота


class TestZKBioLatecomersReport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from access_registry.zkbio.sync import run_server_sync

		cls.early = make_hr_person("Тестскудопозд", "Ранний")
		cls.late = make_hr_person("Тестскудопозд", "Поздний")
		cls.on_leave = make_hr_person("Тестскудопозд", "Отпускная")
		make_server("TSKDLT")
		run_server_sync(
			"TSKDLT",
			"persons",
			commit=False,
			fetch=lambda _s: [
				person_row("l1", "Тестскудопозд", "Ранний", pin="701"),
				person_row("l2", "Тестскудопозд", "Поздний", pin="702"),
				person_row("l3", "Тестскудопозд", "Отпускная", pin="703"),
			],
		)

		def dt(day, hour, minute=0):
			return frappe.utils.get_datetime(f"{day} {hour:02d}:{minute:02d}:00")

		events = [
			event_row("lm1", dt(MON, 8, 55), pin="701"),
			event_row("lm2", dt(MON, 9, 0), pin="701"),
			event_row("lm3", dt(MON, 9, 20), pin="702"),
			event_row("lm4", dt(SAT, 10, 0), pin="702"),
			event_row("lm5", dt(MON, 9, 30), pin="703"),
		]
		run_server_sync("TSKDLT", "events", commit=False, fetch=fetch_with(events))
		if not frappe.db.exists("Info Base", "ZUP1"):
			frappe.get_doc(
				{
					"doctype": "Info Base",
					"source_code": "ZUP1",
					"title": "ZUP1",
					"base_url": "http://127.0.0.1:9/hs",
				}
			).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "HR Absence",
				"source": "ZUP1",
				"employment_guid": "g-late",
				"state_code": "ОТ",
				"state": "Отпуск ежегодный",
				"category": "Отпуск",
				"date_from": MON,
				"person": cls.on_leave.name,
				"record_key": f"ZUP1:g-late:ОТ:{MON}",
			}
		).insert(ignore_permissions=True)

	def test_late_minutes_and_only_late_default(self):
		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		_columns, rows = execute({"date_from": MON, "date_to": MON})
		self.assertEqual([r["person"] for r in rows], [self.late.name])
		self.assertEqual(rows[0]["late_minutes"], 20)
		self.assertEqual(rows[0]["first_event"], "09:20")

	def test_only_late_off_shows_all(self):
		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		_columns, rows = execute({"date_from": MON, "date_to": MON, "only_late": 0})
		persons = {r["person"] for r in rows}
		self.assertIn(self.early.name, persons)
		self.assertIn(self.late.name, persons)
		early_row = [r for r in rows if r["person"] == self.early.name][0]
		self.assertFalse(early_row["late_minutes"])

	def test_threshold_filter(self):
		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		_columns, rows = execute({"date_from": MON, "date_to": MON, "threshold": "9:10"})
		self.assertEqual(rows[0]["late_minutes"], 10)

	def test_exact_threshold_is_not_late(self):
		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		# первое событие Раннего ровно в порог — не опоздание (строго позже)
		_columns, rows = execute({"date_from": MON, "date_to": MON, "threshold": "08:55"})
		self.assertNotIn(self.early.name, {r["person"] for r in rows})

	def test_weekends_hidden_by_default(self):
		import datetime

		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		_columns, rows = execute({"date_from": MON, "date_to": SAT, "only_late": 0})
		self.assertEqual({r["day"] for r in rows}, {datetime.date(2026, 11, 23)})
		_columns, rows = execute({"date_from": MON, "date_to": SAT, "only_late": 0, "show_weekends": 1})
		self.assertIn(datetime.date(2026, 11, 28), {r["day"] for r in rows})

	def test_absent_excluded(self):
		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		_columns, rows = execute({"date_from": MON, "date_to": MON, "only_late": 0})
		self.assertNotIn(self.on_leave.name, {r["person"] for r in rows})
		_columns, rows = execute({"date_from": MON, "date_to": MON, "only_late": 0, "show_absent": 1})
		mine = [r for r in rows if r["person"] == self.on_leave.name]
		self.assertEqual(mine[0]["absence"], "Отпуск ежегодный")

	def test_default_dates_yesterday(self):
		from access_registry.zkbio.report.zkbio_latecomers.zkbio_latecomers import execute

		yesterday = frappe.utils.add_days(frappe.utils.getdate(), -1)
		_columns, default_rows = execute({})
		_columns, explicit_rows = execute({"date_from": yesterday, "date_to": yesterday})
		self.assertEqual(
			{(r["person"], r["day"]) for r in default_rows},
			{(r["person"], r["day"]) for r in explicit_rows},
		)
