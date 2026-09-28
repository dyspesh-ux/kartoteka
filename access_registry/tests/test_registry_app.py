"""Tests of the end-user app /registry: API methods, permissions and the page itself."""

import copy

import frappe

from access_registry.bitrix24.sync import run_portal_sync
from access_registry.registry import api
from access_registry.tests.test_b24 import PORTAL, portal_data
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_roles import RoleFixture


class TestRegistryApp(RoleFixture):
	def setUp(self):
		super().setUp()
		frappe.cache().delete_value(api.CACHE_KEY)
		frappe.get_doc(
			{"doctype": "B24 Portal", "portal_code": PORTAL, "webhook": "https://b24.example.local/rest/1/x/"}
		).insert()
		data = portal_data()
		log = run_portal_sync(PORTAL, commit=False, fetch=lambda _p: copy.deepcopy(data))
		self.assertEqual(log.status, "Успех", log.messages)
		self.base_model()
		process = frappe.get_doc(
			{"doctype": "Business Process", "title": "Закрытие месяца", "status": "Действует"}
		).insert()
		self.process = process.name
		frappe.get_doc(
			{
				"doctype": "Process Role",
				"business_process": process.name,
				"role_name": "Исполнитель",
				"entitlements": [{"entitlement": self.buh, "requirement": "Обязательно"}],
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "SoD Rule",
				"title": "Кадры и учёт",
				"side_a": [{"entitlement": self.buh}],
				"side_b": [{"entitlement": self.kadr}],
			}
		).insert()

	def tearDown(self):
		frappe.cache().delete_value(api.CACHE_KEY)
		super().tearDown()

	def test_bootstrap_and_dashboard(self):
		boot = api.bootstrap()
		self.assertTrue(boot["can"]["admin"])
		self.assertEqual(boot["layers"]["b24"], 1)
		d = api.dashboard(refresh=1)
		self.assertEqual(d["people"]["working"], 6)
		# Фёдоров (1С, AD, Битрикс24) and Новиков (AD) are dismissed and still have access
		self.assertEqual(d["dismissed_access"]["people"], 2)
		self.assertEqual(d["dismissed_access"]["by_system"], {"1С": 1, "AD": 2, "Битрикс24": 1})
		self.assertEqual(d["unlinked"]["Битрикс24"], 1)
		self.assertEqual(d["sod"], 1)
		self.assertTrue(d["reconciliation"]["enabled"])
		self.assertGreater(d["reconciliation"]["missing"], 0)
		self.assertEqual({s["kind"] for s in d["sources"]}, {"Кадры ЗУП", "Active Directory", "Битрикс24"})
		self.assertEqual(api.dashboard(), d)  # cached

	def test_people_and_person(self):
		rows = api.people(status="Уволен")["rows"]
		fedorov = next(r for r in rows if r["full_name"] == "Фёдоров Фёдор Фёдорович")
		self.assertIn("dismissed_access", fedorov["flags"])
		self.assertEqual(api.people(flag="dismissed_access")["total"], 2)
		self.assertEqual(api.people(query="петр")["rows"][0]["full_name"], "Петрова Мария Сергеевна")

		card = api.person(self.person(2))
		self.assertEqual(card["person"]["birth_date"], "1985-03-10")
		self.assertEqual([a.base_code for a in card["ib"]], ["TST1"])
		self.assertEqual(card["ad"][0].sam_account_name, "petrova")
		self.assertEqual(card["b24"][0].portal, PORTAL)
		self.assertIn("Настройки CRM", [a["resource"] for a in card["b24_access"]])
		self.assertIn("Главный бухгалтер", [r["role"] for r in card["roles"]])
		self.assertEqual([s["title"] for s in card["sod"]], ["Кадры и учёт"])
		self.assertTrue(card["reconciliation"])

	def test_catalog_roles_processes(self):
		ents = {e.title: e for e in api.entitlements()}
		self.assertEqual(ents["1С: Кадровик"].holders or 0, 0)  # counters are refreshed nightly
		self.assertEqual(ents["1С: Бухгалтер"].roles, 1)
		detail = api.entitlement(self.kadr)
		self.assertEqual(
			{h["full_name"] for h in detail["holders"]}, {"Иванов Иван Иванович", "Петрова Мария Сергеевна"}
		)
		roles = {r.name: r for r in api.roles()}
		self.assertEqual(roles["Все сотрудники"].members, 6)
		role = api.role("Главный бухгалтер")
		self.assertEqual([m["full_name"] for m in role["members"]], ["Петрова Мария Сергеевна"])
		self.assertEqual(role["doc"]["rules"][0]["position_title"], "главный БУХГАЛТЕР")
		procs = api.processes()
		self.assertEqual(procs[0].risks, 1)  # «Исполнитель» has nobody
		process = api.process(self.process)
		self.assertIn("нет участников", process["roles"][0].problems)

	def test_control_lists(self):
		for kind in api.CONTROLS:
			result = api.control(kind)
			self.assertEqual(result["kind"], kind)
			for row in result["rows"]:
				for column in result["columns"]:
					self.assertIn(column["key"], row, (kind, column["key"]))
		dismissed = api.control("dismissed")["rows"]
		self.assertEqual(
			{r["system"] for r in dismissed if r["full_name"] == "Фёдоров Фёдор Фёдорович"},
			{"1С", "AD", "Битрикс24"},
		)
		self.assertIn("Битрикс24", {r["system"] for r in api.control("unlinked")["rows"]})
		self.assertRaises(frappe.ValidationError, api.control, "nope")

	def test_search_and_exception(self):
		kinds = {(r["kind"], r["title"]) for r in api.search("Петров")}
		self.assertIn(("person", "Петрова Мария Сергеевна"), kinds)
		self.assertIn(("account", "Смирнов Олег"), {(r["kind"], r["title"]) for r in api.search("Смирнов")})
		self.assertIn(
			("role", "Главный бухгалтер"), {(r["kind"], r["title"]) for r in api.search("бухгалтер")}
		)
		name = api.create_exception(self.person(1), self.kadr, "Замещение", None)
		self.assertTrue(frappe.db.exists("Access Exception", name))
		self.assertRaises(frappe.ValidationError, api.create_exception, self.person(1), self.kadr, "  ")

	def test_permissions_and_page(self):
		viewer = make_user("viewer-app@registry.test", "Access Catalog Viewer")
		outsider = make_user("outsider@registry.test", "Blogger")
		frappe.set_user(viewer)
		self.assertFalse(api.bootstrap()["can"]["personal"])
		self.assertIsNone(api.person(self.person(2))["person"]["birth_date"])
		self.assertRaises(frappe.PermissionError, api.create_exception, self.person(1), self.kadr, "x")
		frappe.set_user(outsider)
		self.assertRaises(frappe.PermissionError, api.dashboard)

		from access_registry.www import registry

		frappe.set_user("Guest")
		self.assertRaises(frappe.Redirect, registry.get_context, frappe._dict())
		frappe.set_user(viewer)
		context = frappe._dict()
		registry.get_context(context)
		self.assertTrue(context.allowed)
		self.assertFalse(context.desk)
		frappe.set_user("Administrator")
		from frappe.website.serve import get_response_content

		html = get_response_content("registry")
		self.assertIn("access_registry.registry.api.", html)
		self.assertIn("--accent", html)

	def test_hr_events_to_do(self):
		for n, kind in ((8, "Увольнение"), (3, "Приём"), (6, "Уход в отпуск по уходу")):
			frappe.get_doc(
				{
					"doctype": "HR Event",
					"event_type": kind,
					"person": self.person(n),
					"event_date": "2026-06-01",
					"source": "TST1",
				}
			).insert(ignore_permissions=True)
		rows = {r.full_name: r.todo for r in api.control("events")["rows"]}
		self.assertIn("Отключить учётки: 1С, AD, Битрикс24", rows["Фёдоров Фёдор Фёдорович"])
		self.assertIn("Выдать: AD: пользователи ЗУП", rows["Сидоров Пётр Алексеевич"])
		self.assertIn("блокировать", rows["Орлова Елена Павловна"])
		self.assertEqual(api.dashboard(refresh=1)["events"], 3)
		event = frappe.db.get_value("HR Event", {"person": self.person(8)}, "name")
		api.mark_event_processed(event)
		self.assertEqual(frappe.db.get_value("HR Event", event, "processed"), 1)
		self.assertNotIn("Фёдоров Фёдор Фёдорович", {r.full_name for r in api.control("events")["rows"]})
