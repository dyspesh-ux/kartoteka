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
