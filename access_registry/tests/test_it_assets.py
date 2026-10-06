"""Tests of the Snipe-IT mirror: users linked to employees, hardware, the activity log, the guard."""

import copy
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.active_directory.sync import run_domain_sync
from access_registry.it_assets import sync as snipe
from access_registry.it_assets.client import SnipeITClient
from access_registry.sync.departments import ensure_root
from access_registry.sync.engine import run_source_sync
from access_registry.tests.test_ad import AD_DOCTYPES, directory
from access_registry.tests.test_catalog import HR_DOCTYPES, S1
from access_registry.tests.test_zup_sync import TODAY, load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "snipeit")
SERVER = "SNIPE"
IT_DOCTYPES = ["IT Asset Event", "IT Asset", "Snipe-IT User", "Snipe-IT Server"]


def snipe_data() -> dict:
	data = {}
	for name in ("users", "hardware", "activity"):
		with open(os.path.join(FIXTURES, f"{name}.json"), encoding="utf-8") as fh:
			data[name] = json.load(fh)["rows"]
	return data


class SnipeFixture(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in IT_DOCTYPES + AD_DOCTYPES + HR_DOCTYPES:
			frappe.db.delete(doctype)
		frappe.db.delete("Version", {"ref_doctype": ["in", ["IT Asset", "Snipe-IT User"]]})
		ensure_root()
		frappe.get_doc(
			{"doctype": "Info Base", "source_code": S1, "title": S1, "base_url": "http://127.0.0.1:9/hs"}
		).insert()
		log = run_source_sync(S1, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Успех", log.messages)
		frappe.get_doc(
			{
				"doctype": "AD Domain",
				"domain_code": "TSTAD",
				"netbios_name": "CORP",
				"ldap_url": "ldap://dc1",
				"base_dn": "DC=corp,DC=example,DC=local",
				"bind_user": "svc",
			}
		).insert()
		log = run_domain_sync("TSTAD", commit=False, fetch=lambda _d: directory())
		self.assertEqual(log.status, "Успех", log.messages)
		frappe.get_doc(
			{
				"doctype": "Snipe-IT Server",
				"server_code": SERVER,
				"title": "Учёт техники",
				"base_url": "https://snipeit.example.local",
				"activity_days": 3650,
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def sync(self, data=None):
		payload = copy.deepcopy(data or snipe_data())
		return snipe.run_server_sync(SERVER, commit=False, fetch=lambda _s: payload)

	def person(self, last_name):
		return frappe.db.get_value("Person", {"full_name": ["like", f"{last_name}%"]}, "name")


class TestSnipeITSync(SnipeFixture):
	def test_users_linked_to_employees(self):
		log = self.sync()
		self.assertEqual(log.status, "Успех", log.messages)
		users = {
			u.username or u.full_name: u
			for u in frappe.get_all(
				"Snipe-IT User",
				fields=["username", "full_name", "person", "person_link_method", "person_link_note"],
			)
		}
		self.assertEqual(
			(users["ivanov"].person, users["ivanov"].person_link_method), (self.person("Иванов"), "Учётка AD")
		)
		self.assertEqual(
			(users["m.petrova"].person, users["m.petrova"].person_link_method),
			(self.person("Петрова"), "Почта"),
		)
		fedorov = users["Фёдор Фёдоров"]
		self.assertEqual(
			(fedorov.person, fedorov.person_link_method), (self.person("Фёдоров"), "Табельный номер")
		)
		self.assertEqual(users["Елена Павловна Орлова"].person, self.person("Орлова"))
		self.assertEqual(users["Дмитрий Новиков"].person, self.person("Новиков"))
		self.assertFalse(users["contractor"].person)
		self.assertIn("не найден", users["contractor"].person_link_note)

	def test_hardware_and_activity(self):
		self.sync()
		nb = frappe.get_doc("IT Asset", f"{SERVER}:101")
		self.assertEqual(nb.asset_name, 'Ноутбук "Иванов"')  # HTML entities of the API are decoded
		self.assertEqual(nb.company, 'ООО "Ромашка"')
		self.assertEqual((nb.assigned_type, nb.person), ("user", self.person("Иванов")))
		self.assertEqual(nb.purchase_cost, 85000)
		self.assertIn("MAC: AA:BB:CC:00:01:01", nb.custom_fields)
		self.assertEqual(frappe.db.get_value("IT Asset", f"{SERVER}:102", "asset_name"), "ThinkPad T14")
		monitor = frappe.get_doc("IT Asset", f"{SERVER}:104")
		self.assertEqual(
			(monitor.assigned_type, monitor.assigned_name, monitor.person), ("location", "Переговорная", None)
		)
		self.assertEqual(frappe.db.get_value("IT Asset", f"{SERVER}:107", "status_type"), "archived")
		events = frappe.get_all(
			"IT Asset Event", fields=["action", "asset", "person"], order_by="event_date desc"
		)
		self.assertEqual(len(events), 4)  # the one older than activity_days is skipped
		self.assertEqual((events[0].action, events[0].asset), ("Выдача", f"{SERVER}:101"))
		self.assertEqual(events[0].person, self.person("Иванов"))

	def test_second_load_changes_nothing_and_history(self):
		self.sync()
		log = self.sync()
		stats = json.loads(log.stats)
		self.assertFalse([k for k in stats if k.endswith(("создано", "изменено"))], stats)
		self.assertEqual(stats["журнал: новых записей"], 0)
		data = snipe_data()
		data["hardware"][0]["assigned_to"] = None  # the laptop returned
		data["hardware"] = [a for a in data["hardware"] if a["id"] != 107]  # the archived one deleted
		self.sync(data)
		self.assertIsNone(frappe.db.get_value("IT Asset", f"{SERVER}:101", "person"))
		self.assertEqual(frappe.db.get_value("IT Asset", f"{SERVER}:107", "missing_in_source"), 1)
		versions = frappe.get_all(
			"Version", filters={"ref_doctype": "IT Asset", "docname": f"{SERVER}:101"}, pluck="data"
		)
		self.assertTrue(any("assigned_name" in v for v in versions))

	def test_manual_link_and_guard(self):
		self.sync()
		uid = f"{SERVER}:4"
		doc = frappe.get_doc("Snipe-IT User", uid)
		doc.manual_person = self.person("Сидоров")
		doc.save()
		self.sync()
		self.assertEqual(frappe.db.get_value("Snipe-IT User", uid, "person"), self.person("Сидоров"))
		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 1)
		data = snipe_data()
		data["hardware"] = data["hardware"][:1]
		log = self.sync(data)
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertEqual(frappe.db.count("IT Asset", {"missing_in_source": 0}), 8)


class FakeResponse:
	def __init__(self, data, status=200):
		self.data, self.status_code, self.text = data, status, json.dumps(data)

	def json(self):
		return self.data


class FakeSession:
	def __init__(self, rows):
		self.rows, self.calls, self.headers = rows, [], {}

	def get(self, url, params=None, timeout=None, verify=None):
		self.calls.append((url, dict(params)))
		offset, limit = params["offset"], params["limit"]
		return FakeResponse({"total": len(self.rows), "rows": self.rows[offset : offset + limit]})


class TestSnipeITClient(FrappeTestCase):
	def test_paging_and_errors(self):
		session = FakeSession([{"id": i} for i in range(1, 1201)])
		client = SnipeITClient("https://snipeit.example.local/api/v1/", "token", session=session)
		self.assertEqual(len(client.hardware()), 1200)
		self.assertEqual([c[1]["offset"] for c in session.calls], [0, 500, 1000])
		self.assertEqual(session.calls[0][0], "https://snipeit.example.local/api/v1/hardware")
		self.assertEqual(session.headers["Authorization"], "Bearer token")

		class Denied(FakeSession):
			def get(self, url, params=None, timeout=None, verify=None):
				return FakeResponse({"status": "error"}, status=401)

		with self.assertRaises(Exception) as ctx:
			SnipeITClient("https://x", "bad", session=Denied([])).hardware()
		self.assertIn("ключ API не принят", str(ctx.exception))


class TestSnipeITInApp(SnipeFixture):
	def test_control_list_card_overview_reports(self):
		from access_registry.it_assets import reports
		from access_registry.registry import api

		self.sync()
		rows = api.control("assets")["rows"]
		issues = {(r["asset_tag"], r["issue"]) for r in rows}
		self.assertIn(("NB-0102", "у неработающего"), issues)  # Фёдоров is dismissed
		self.assertIn(("NB-0108", "у неработающего"), issues)  # Новиков
		self.assertIn(("PH-0103", "выдана учётке без сотрудника"), issues)
		self.assertIn(("NB-0106", "просрочен возврат"), issues)
		self.assertIn(("NB-0105", "просрочен аудит"), issues)
		self.assertNotIn("NB-0101", {tag for tag, _issue in issues})
		self.assertTrue(all(r["alert_key"] for r in rows))  # may be suppressed

		card = api.person(self.person("Иванов"))
		self.assertEqual([a["asset_tag"] for a in card["assets"]], ["NB-0101"])
		self.assertEqual(card["asset_events"][0]["action"], "Выдача")

		frappe.cache().delete_value(api.CACHE_KEY)
		assets = api.dashboard(refresh=1)["assets"]
		self.assertEqual((assets["not_working"], assets["unlinked"]), (2, 1))
		self.assertEqual(assets["total"], 7)  # without the archived one

		_columns, rows = reports.assets({"only_not_working": 1})
		self.assertEqual({r["asset_tag"] for r in rows}, {"NB-0102", "NB-0108"})
		_columns, rows = reports.movements(
			{"from_date": "2025-01-01", "to_date": "2026-12-31", "only_handovers": 1}
		)
		self.assertEqual({r["action"] for r in rows}, {"Выдача", "Возврат"})

	def test_profile_limited_to_equipment(self):
		from access_registry import app_access
		from access_registry.registry import api
		from access_registry.tests.test_permissions import make_user

		self.sync()
		user = make_user("assets-admin@registry.test")
		api.save_profile(
			{
				"profile_name": "Учёт техники",
				"sections": {"people": 1, "control": 1, "reports": 1},
				"systems": ["assets"],
				"members": [user],
			}
		)
		frappe.set_user(user)
		self.assertIn("assets", app_access.control_lists())
		self.assertNotIn("shares", app_access.control_lists())
		names = set(app_access.report_names())
		self.assertTrue({"IT Assets", "IT Asset Movements"} <= names)
		# reports of other systems are closed; the role model ones stay with rows of this system only
		self.assertFalse(
			{"IB Access Report", "AD Without Employee", "B24 Section Access", "Share Access"} & names
		)
		card = api.person(self.person("Иванов"))
		self.assertEqual((card["ib"], card["ad"]), ([], []))
		self.assertEqual(len(card["assets"]), 1)
		self.assertTrue(all(r["system"] == "Техника" for r in api.control("unlinked")["rows"]))
