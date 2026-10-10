"""Tests of the role model (entitlements, access roles, reconciliation, SoD, role mining) and processes."""

import copy

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from access_registry.access_catalog import importer
from access_registry.access_roles import engine
from access_registry.access_roles import reports as role_reports
from access_registry.active_directory.sync import run_domain_sync
from access_registry.business_processes import reports as process_reports
from access_registry.sync.departments import ensure_root
from access_registry.sync.engine import run_source_sync
from access_registry.tests.test_ad import AD_DOCTYPES, GRP, directory
from access_registry.tests.test_b24 import B24_DOCTYPES, PORTAL, portal_data
from access_registry.tests.test_catalog import CATALOG_DOCTYPES, FL, HR_DOCTYPES, PRF, S1, snapshot
from access_registry.tests.test_zup_sync import TODAY, load

ROLE_DOCTYPES = [
	"Process Participant",
	"Process Role Entitlement",
	"Process Role",
	"Business Process",
	"SoD Rule Entitlement",
	"SoD Rule",
	"Access Exception",
	"Access Role Assignment",
	"Access Role Entitlement",
	"Access Role Rule",
	"Access Role",
	"Entitlement",
]
DOMAIN = "TSTAD"


class RoleFixture(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in ROLE_DOCTYPES + B24_DOCTYPES + AD_DOCTYPES + CATALOG_DOCTYPES + HR_DOCTYPES:
			frappe.db.delete(doctype)
		ensure_root()
		frappe.get_doc(
			{"doctype": "Info Base", "source_code": S1, "title": S1, "base_url": "http://127.0.0.1:9/hs"}
		).insert()
		log = run_source_sync(S1, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Успех", log.messages)
		importer.import_snapshot_data(S1, snapshot())
		frappe.get_doc(
			{
				"doctype": "AD Domain",
				"domain_code": DOMAIN,
				"netbios_name": "CORP",
				"ldap_url": "ldap://dc1",
				"base_dn": "DC=corp,DC=example,DC=local",
				"bind_user": "svc",
			}
		).insert()
		ad = directory()
		log = run_domain_sync(DOMAIN, commit=False, fetch=lambda _d: ad)
		self.assertEqual(log.status, "Успех", log.messages)

		self.kadr = self.entitlement("1С: Кадровик", "1С", ib_profile=f"{S1}:{PRF(1)}")
		self.buh = self.entitlement("1С: Бухгалтер", "1С", ib_profile=f"{S1}:{PRF(2)}")
		self.empty = self.entitlement("1С: Пустой профиль", "1С", ib_profile=f"{S1}:{PRF(5)}")
		self.zup_group = self.entitlement(
			"AD: пользователи ЗУП", "Active Directory", ad_group=f"{DOMAIN}:{GRP(1)}"
		)
		self.buh_read = self.entitlement(
			"AD: папка бухгалтерии", "Active Directory", ad_group=f"{DOMAIN}:{GRP(2)}"
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def entitlement(self, title, system, **links):
		return (
			frappe.get_doc({"doctype": "Entitlement", "title": title, "system": system, **links})
			.insert()
			.name
		)

	def person(self, n):
		return frappe.db.get_value("Person Source ID", {"source": S1, "person_guid": FL(n)}, "parent")

	def role(self, name, kind="Должностная", rules=(), entitlements=(), status="Действует"):
		return frappe.get_doc(
			{
				"doctype": "Access Role",
				"role_name": name,
				"kind": kind,
				"status": status,
				"rules": list(rules),
				"entitlements": [{"entitlement": e, "requirement": r} for e, r in entitlements],
			}
		).insert()

	def base_model(self):
		self.role(
			"Главный бухгалтер",
			rules=[{"position_title": "главный БУХГАЛТЕР"}],
			entitlements=[
				(self.buh, "Обязательно"),
				(self.buh_read, "Обязательно"),
				(self.kadr, "По необходимости"),
			],
		)
		self.role("Все сотрудники", kind="Базовая", entitlements=[(self.zup_group, "Обязательно")])
		self.role("Кадровик", kind="Дополнительная", entitlements=[(self.kadr, "Обязательно")])

	def statuses(self, person):
		return {r["title"]: r["status"] for r in engine.reconcile({person})}


class TestRoleModel(RoleFixture):
	# ---------------------------------------------------------------- roles by rules

	def test_rules(self):
		accounting = frappe.db.get_value("HR Department", {"title": "Бухгалтерия"}, "name")
		self.role("Бухгалтерия (все)", rules=[{"department": accounting, "include_subdepartments": 1}])
		self.role("Бухгалтерия (основные)", rules=[{"department": accounting, "main_only": 1}])
		model = engine.RoleModel()
		all_members = model.members_of_role("Бухгалтерия (все)")
		self.assertEqual(set(all_members), {self.person(2), self.person(4)})  # Кузнецова — совместитель
		self.assertEqual(set(model.members_of_role("Бухгалтерия (основные)")), {self.person(2)})
		# dismissed people get no roles by rules
		self.role("Кладовщики", rules=[{"position_title": "Кладовщик"}])
		self.assertEqual(set(engine.RoleModel().members_of_role("Кладовщики")), {self.person(6)})
		self.assertRaises(frappe.ValidationError, self.role, "Пустая", rules=[{}])
		self.assertRaises(frappe.ValidationError, self.role, "Без правил")

	# ---------------------------------------------------------------- reconciliation

	def test_reconciliation(self):
		self.base_model()
		petrova = self.statuses(self.person(2))
		self.assertEqual(
			petrova,
			{
				"1С: Бухгалтер": "Соответствует",
				"1С: Кадровик": "Соответствует",
				"AD: папка бухгалтерии": "Соответствует",
				"AD: пользователи ЗУП": "Соответствует",
			},
		)
		ivanov = self.statuses(self.person(1))
		self.assertEqual(ivanov["1С: Кадровик"], "Лишнее")
		self.assertEqual(self.statuses(self.person(3))["AD: пользователи ЗУП"], "Не хватает")
		fedorov = self.statuses(self.person(8))
		self.assertEqual(set(fedorov.values()), {"Лишнее: не работает"})
		self.assertIn("1С: Пустой профиль", fedorov)

		# manual assignment of an additional role
		assignment = frappe.get_doc(
			{
				"doctype": "Access Role Assignment",
				"person": self.person(1),
				"access_role": "Кадровик",
				"valid_to": add_days(today(), 30),
				"reason": "Заявка 15",
			}
		).insert()
		self.assertEqual(assignment.approved_by, "Administrator")
		self.assertEqual(self.statuses(self.person(1))["1С: Кадровик"], "Соответствует")
		assignment.valid_to = add_days(today(), -1)
		assignment.save()
		self.assertEqual(self.statuses(self.person(1))["1С: Кадровик"], "Лишнее")

		# approved exception
		frappe.get_doc(
			{
				"doctype": "Access Exception",
				"person": self.person(1),
				"entitlement": self.kadr,
				"reason": "Замещает кадровика до конца месяца",
				"valid_to": add_days(today(), 10),
			}
		).insert()
		self.assertEqual(self.statuses(self.person(1))["1С: Кадровик"], "Исключение")

		columns, rows = role_reports.reconciliation({"status": "Не хватает"})
		self.assertTrue(rows)
		self.assertEqual({r["status"] for r in rows}, {"Не хватает"})
		self.assertNotIn("Соответствует", {r["status"] for r in role_reports.reconciliation({})[1]})
		rows = role_reports.reconciliation({"access_role": "Главный бухгалтер", "show_ok": 1})[1]
		self.assertEqual({r["person"] for r in rows}, {self.person(2)})

	def test_draft_roles_do_not_count(self):
		self.role(
			"Черновик",
			rules=[{"position_title": "Главный бухгалтер"}],
			entitlements=[(self.empty, "Обязательно")],
			status="Черновик",
		)
		self.assertNotIn("1С: Пустой профиль", self.statuses(self.person(2)))

	def test_sod(self):
		frappe.get_doc(
			{
				"doctype": "SoD Rule",
				"title": "Кадры и учёт",
				"severity": "Критичная",
				"side_a": [{"entitlement": self.buh}],
				"side_b": [{"entitlement": self.kadr}],
			}
		).insert()
		rows = role_reports.sod_conflicts({})[1]
		self.assertEqual([r["person"] for r in rows], [self.person(2)])
		self.assertEqual(rows[0]["side_a"], "1С: Бухгалтер")
		self.assertRaises(
			frappe.ValidationError,
			frappe.get_doc(
				{
					"doctype": "SoD Rule",
					"title": "Плохое",
					"side_a": [{"entitlement": self.buh}],
					"side_b": [{"entitlement": self.buh}],
				}
			).insert,
		)
		self.role(
			"Опасная",
			kind="Дополнительная",
			entitlements=[(self.buh, "Обязательно"), (self.kadr, "Обязательно")],
		)
		self.assertEqual(
			engine.role_design_conflicts(),
			[{"rule": "Кадры и учёт", "doctype": "Access Role", "name": "Опасная"}],
		)

	def test_duplicate_entitlement(self):
		self.assertRaises(
			frappe.ValidationError, self.entitlement, "Ещё раз Кадровик", "1С", ib_profile=f"{S1}:{PRF(1)}"
		)

	# ---------------------------------------------------------------- initial reconciliation

	def test_role_mining_and_catalog(self):
		frappe.db.delete(
			"Entitlement", {"name": ["in", [self.buh, self.buh_read, self.zup_group, self.empty]]}
		)
		unmanaged = {r["title"] for r in role_reports.unmanaged_access({})[1]}
		self.assertIn("1С TST1: Бухгалтер", unmanaged)
		self.assertIn("AD TSTAD: DL_Buh_Read", unmanaged)
		self.assertNotIn("AD TSTAD: Рассылка всем", unmanaged)  # distribution lists give no access
		self.assertNotIn("1С TST1: Кадровик", unmanaged)  # already in the catalog

		rows = role_reports.role_mining({"min_people": 1, "threshold": 100})[1]
		chief = {r["title"] for r in rows if r["position"] == "Главный бухгалтер"}
		self.assertEqual(
			chief,
			{"1С TST1: Бухгалтер", "1С TST1: Кадровик", "AD TSTAD: DL_Buh_Read", "AD TSTAD: GG_1C_ZUP_Users"},
		)

		self.role("Главный бухгалтер", rules=[{"position_title": "Главный бухгалтер"}])
		created = engine.create_draft_roles(min_people=1, threshold=1.0)
		self.assertIn("Должность: Генеральный директор", created)
		self.assertNotIn("Должность: Главный бухгалтер", created)  # a role for the position already exists
		director = frappe.get_doc("Access Role", "Должность: Генеральный директор")
		self.assertEqual(director.status, "Черновик")
		self.assertEqual(director.rules[0].position_title, "Генеральный директор")
		self.assertIn(
			self.kadr, [r.entitlement for r in director.entitlements]
		)  # existing entitlement reused
		self.assertEqual(engine.create_draft_roles(min_people=1, threshold=1.0), [])  # idempotent

		engine.refresh_counters()
		self.assertEqual(frappe.db.get_value("Entitlement", self.kadr, "holders"), 2)

	def test_bitrix24_entitlements(self):
		from access_registry.bitrix24.sync import run_portal_sync

		frappe.get_doc(
			{"doctype": "B24 Portal", "portal_code": PORTAL, "webhook": "https://b24.example.local/rest/1/x/"}
		).insert()
		data = portal_data()
		log = run_portal_sync(PORTAL, commit=False, fetch=lambda _p: copy.deepcopy(data))
		self.assertEqual(log.status, "Успех", log.messages)
		crm = self.entitlement("Битрикс24: CRM менеджер", "Битрикс24", b24_via="Роль CRM «Менеджер»")
		project = self.entitlement("Битрикс24: проект внедрения", "Битрикс24", b24_workgroup=f"{PORTAL}:10")
		actual, _other = engine.actual_entitlements()
		self.assertIn(crm, actual[self.person(3)])  # through the department «Отдел продаж»
		self.assertIn(crm, actual[self.person(8)])  # directly (U4)
		self.assertIn(project, actual[self.person(2)])
		self.assertNotIn(crm, actual.get(self.person(2), {}))


class TestProcesses(RoleFixture):
	def test_regulation_link_is_a_web_address(self):
		process = frappe.get_doc(
			{"doctype": "Business Process", "title": "Регламент", "regulation_url": " https://wiki/x "}
		).insert()
		self.assertEqual(process.regulation_url, "https://wiki/x")
		process.regulation_url = "/files/regulation.pdf"
		process.save()
		for bad in ("javascript:alert(1)", "javascript://wiki/%0Aalert(1)", "data:text/html,x", "//evil/x"):
			process.regulation_url = bad
			self.assertRaises(frappe.ValidationError, process.save)

	def process_model(self):
		self.base_model()
		process = frappe.get_doc(
			{
				"doctype": "Business Process",
				"title": "Закрытие месяца",
				"process_code": "ФИН-01",
				"status": "Действует",
			}
		).insert()
		checker = frappe.get_doc(
			{
				"doctype": "Process Role",
				"business_process": process.name,
				"role_name": "Проверяющий",
				"raci": "Отвечает за результат (A)",
				"filled_by_access_role": "Главный бухгалтер",
				"needs_deputy": 1,
				"entitlements": [{"entitlement": self.buh_read, "requirement": "Обязательно"}],
			}
		).insert()
		doer = frappe.get_doc(
			{
				"doctype": "Process Role",
				"business_process": process.name,
				"role_name": "Исполнитель",
				"needs_deputy": 1,
				"entitlements": [{"entitlement": self.buh, "requirement": "Обязательно"}],
			}
		).insert()
		return process, checker, doer

	def participant(self, role, n, participation="Основной"):
		return frappe.get_doc(
			{
				"doctype": "Process Participant",
				"process_role": role.name,
				"person": self.person(n),
				"participation": participation,
			}
		).insert()

	def test_process_roles_bring_entitlements(self):
		process, checker, doer = self.process_model()
		self.participant(doer, 3)
		model = engine.RoleModel()
		self.assertEqual(
			model.process_roles_of(self.person(2)), {checker.name: "по роли доступа «Главный бухгалтер»"}
		)
		sidorov = self.statuses(self.person(3))
		self.assertEqual(sidorov["1С: Бухгалтер"], "Не хватает")
		missing = [r for r in engine.reconcile({self.person(3)}) if r["title"] == "1С: Бухгалтер"][0]
		self.assertIn("Закрытие месяца", missing["expected_by"])
		self.assertEqual(
			frappe.db.get_value("Process Participant", {"person": self.person(3)}, "business_process"),
			process.name,
		)
		self.assertRaises(frappe.ValidationError, self.participant, doer, 3)
		self.assertRaises(
			frappe.ValidationError,
			frappe.get_doc(
				{"doctype": "Process Role", "business_process": process.name, "role_name": "Исполнитель"}
			).insert,
		)
		# an archived process brings nothing
		frappe.db.set_value("Business Process", process.name, "status", "Архив")
		self.assertNotIn("1С: Бухгалтер", self.statuses(self.person(3)))

	def test_continuity_and_participants(self):
		_process, checker, doer = self.process_model()
		self.participant(doer, 8)  # dismissed
		rows = {r["role_name"]: r["problems"] for r in process_reports.continuity({})[1]}
		self.assertIn("не работают: Фёдоров Фёдор Фёдорович", rows["Исполнитель"])
		self.assertIn("доступно участников 0 из 1", rows["Исполнитель"])
		self.assertIn("нет заместителя", rows["Проверяющий"])  # only Петрова
		self.participant(checker, 1, "Заместитель")
		rows = {r["role_name"]: r["problems"] for r in process_reports.continuity({})[1]}
		self.assertNotIn("Проверяющий", rows)
		participants = process_reports.participants({"only_problems": 1})[1]
		self.assertEqual([r["full_name"] for r in participants], ["Фёдоров Фёдор Фёдорович"])
		for module in ("Access Roles", "Business Processes"):
			for report in frappe.get_all("Report", filters={"module": module}, pluck="name"):
				frappe.get_doc("Report", report).execute_script_report({})
