"""Recruiting («Подбор и закупка»): fields of a recruiting smart process, references, vacancies and the
equipment to buy. Synthetic data shaped like a real recruiting funnel."""

from datetime import timedelta

import frappe
from frappe.utils import add_days, now_datetime, today

from access_registry.bitrix24 import hiring, smart_items
from access_registry.bitrix24.client import B24Error
from access_registry.registry import api
from access_registry.tests.test_it_assets import SnipeFixture
from access_registry.tests.test_permissions import make_user

PORTAL = "hire-test"
ETID = 1074
POS, POS_NEW, DEP_OLD, DEP, EQUIP, LEVEL, OFFICE, START, SERVICES, REMOTE, MOBILE, SALARY = (
	"ufCrm18_1",
	"ufCrm18_2",
	"ufCrm18_3",
	"ufCrm18_4",
	"ufCrm18_5",
	"ufCrm18_6",
	"ufCrm18_7",
	"ufCrm18_8",
	"ufCrm18_9",
	"ufCrm18_10",
	"ufCrm18_11",
	"ufCrm18_12",
)
FIELDS = {
	"title": {"type": "string", "title": "Название"},
	"mycompanyId": {"type": "crm_company", "title": "Реквизиты вашей компании"},
	"assignedById": {"type": "user", "title": "Ответственный"},
	POS: {"type": "crm", "title": "Наименование должности:", "settings": {"DYNAMIC_1086": "Y"}},
	POS_NEW: {"type": "string", "title": "Новая должность"},
	DEP_OLD: {
		"type": "iblock_element",
		"title": "Подразделение, в котором открыта вакансия:**",
		"settings": {"IBLOCK_ID": 31},
	},
	DEP: {
		"type": "crm",
		"title": "Подразделение, в котором открыта вакансия:",
		"isRequired": True,
		"settings": {"DYNAMIC_1116": "Y"},
	},
	EQUIP: {"type": "string", "title": "Оборудование для рабочего места сотрудника", "isRequired": True},
	"ufCrm18_13": {"type": "boolean", "title": "Создан СП Оборудования"},
	LEVEL: {
		"type": "enumeration",
		"title": "Статус кандидата в компании",
		"isRequired": True,
		"items": [{"ID": "1", "VALUE": "Руководитель отдела"}, {"ID": "2", "VALUE": "Рядовой сотрудник"}],
	},
	OFFICE: {
		"type": "iblock_element",
		"title": "Адрес офиса",
		"isRequired": True,
		"settings": {"IBLOCK_ID": 45},
	},
	START: {"type": "date", "title": "Дата выхода на работу"},
	SERVICES: {
		"type": "enumeration",
		"title": "Сервисы, в которых будет работать сотрудник",
		"isMultiple": True,
		"items": [{"ID": "11", "VALUE": "Почта"}, {"ID": "12", "VALUE": "1С"}],
	},
	REMOTE: {"type": "boolean", "title": "Дистанционная работа?"},
	MOBILE: {"type": "boolean", "title": "Разъездной/подвижной характер работы"},
	SALARY: {"type": "money", "title": "ЗП кандидата на руки", "isRequired": True},
	"ufCrm18_14": {"type": "string", "title": "Телефон кандидата"},
}
STAGES = [
	{"STATUS_ID": "DT1074_21:NEW", "NAME": "Новая заявка", "SEMANTICS": None},
	{"STATUS_ID": "DT1074_21:UC_A", "NAME": "Собеседования", "SEMANTICS": ""},
	{"STATUS_ID": "DT1074_21:UC_B", "NAME": "Подготовка оффера", "SEMANTICS": ""},
	{"STATUS_ID": "DT1074_21:UC_C", "NAME": "Оформлен", "SEMANTICS": ""},
	{"STATUS_ID": "DT1074_21:UC_D", "NAME": "Испытательный срок", "SEMANTICS": ""},
	{"STATUS_ID": "DT1074_21:SUCCESS", "NAME": "Принят в штат", "SEMANTICS": "S"},
	{"STATUS_ID": "DT1074_21:FAIL", "NAME": "Отмена/Уволен", "SEMANTICS": "F"},
]
CATALOG = {1086: {"5": "Бухгалтер", "6": "Инженер"}, 1116: {"7": "Бухгалтерия", "8": "ИТ-отдел"}}
COMPANY_OF_DEP = {"7": "101", "8": "102"}
COMPANIES = {"101": "ООО «Ромашка»", "102": "ООО «Ромашка Сервис»"}


def iso(dt):
	return dt.strftime("%Y-%m-%dT%H:%M:%S+03:00")


def item(
	n, stage, position="5", dep="7", equip="Ноутбук, 2 монитора", company=None, level="2", start=None, days=3
):
	created = now_datetime() - timedelta(days=days)
	return {
		"id": n,
		"title": f"Вакансия {n}",
		"stageId": f"DT1074_21:{stage}",
		"categoryId": 21,
		"createdTime": iso(created),
		"updatedTime": iso(created),
		"movedTime": iso(created),
		"assignedById": 0,
		"mycompanyId": company,
		POS: position,
		POS_NEW: "",
		DEP: f"T45c_{dep}" if dep else None,  # 1116 = 0x45c
		EQUIP: equip,
		LEVEL: level,
		OFFICE: "900",
		START: start or "",
		SERVICES: ["11", "12"],
		REMOTE: "Y" if n == 1 else "N",
		MOBILE: "N",
		SALARY: "150000|RUB",
	}


class FakeClient:
	def __init__(self, items):
		self.items = items
		self.calls = []

	def crm_types(self):
		return [{"entityTypeId": ETID, "title": "HR: Подбор персонала"}]

	def result(self, method, params):
		if method == "crm.item.fields":
			return {"fields": FIELDS}
		if method == "crm.category.list":
			return {"categories": [{"id": 21, "name": "Общая"}]}
		raise AssertionError(method)

	def call_many(self, method, params):
		return [STAGES]

	def list_all(self, method, params, key=None):
		self.calls.append((method, params))
		if method == "crm.item.list" and params["entityTypeId"] == ETID:
			assert SALARY not in params["select"]  # the salary is never read
			wanted = set(params["filter"]["@stageId"])
			return [i for i in self.items if i["stageId"] in wanted]
		if method == "crm.item.list":
			entity = params["entityTypeId"]
			return [
				{"id": int(k), "title": v, "mycompanyId": COMPANY_OF_DEP.get(k) if entity == 1116 else None}
				for k, v in CATALOG[entity].items()
				if k in params["filter"]["@id"]
			]
		if method == "crm.company.list":
			return [{"ID": k, "TITLE": v} for k, v in COMPANIES.items() if k in params["filter"]["@ID"]]
		if method == "lists.element.get":
			if params["IBLOCK_TYPE_ID"] != "lists":
				raise B24Error("ERROR_IBLOCK_NOT_FOUND")
			return [{"ID": "900", "NAME": "Москва, Ленина 1"}]
		raise AssertionError(method)


class TestHiring(SnipeFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("B24 Smart Item")
		frappe.db.delete("B24 Smart Process")
		frappe.db.delete("B24 Portal", PORTAL)
		frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": PORTAL,
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
			}
		).insert()
		self.process = frappe.get_doc(
			{
				"doctype": "B24 Smart Process",
				"portal": PORTAL,
				"entity_type_id": ETID,
				"purpose": smart_items.HIRING,
			}
		).insert()

	def sync_hiring(self, items):
		client = FakeClient(items)
		log = smart_items.run_process_sync(self.process.name, commit=False, client=client)
		self.assertEqual(log.status, "Успех", log.messages)
		return log, client

	def test_detect_prefers_required_fields(self):
		codes = smart_items.detect_fields(FIELDS, roles=smart_items.HIRING_ROLES)
		self.assertEqual(codes["position_field"], POS)
		self.assertEqual(codes["position_alt_field"], POS_NEW)
		self.assertEqual(codes["department_field"], DEP)  # the required one, not the old optional list
		self.assertEqual(codes["organization_field"], "mycompanyId")
		self.assertEqual(codes["equipment_field"], EQUIP)  # not «Создан СП Оборудования»
		self.assertEqual(
			(codes["level_field"], codes["office_field"], codes["start_field"]), (LEVEL, OFFICE, START)
		)
		self.assertEqual(
			(codes["services_field"], codes["remote_field"], codes["mobile_field"]),
			(SERVICES, REMOTE, MOBILE),
		)
		self.assertNotIn(SALARY, codes.values())

	def test_sync_resolves_references(self):
		log, client = self.sync_hiring(
			[item(1, "NEW"), item(2, "UC_B", company="102"), item(3, "UC_C", position=None, dep=None)]
		)
		doc = frappe.get_doc("B24 Smart Process", self.process.name)
		self.assertEqual(doc.hired_stage, "DT1074_21:UC_C")  # «Оформлен»
		one = frappe.get_doc("B24 Smart Item", f"{self.process.name}:1")
		self.assertEqual((one.position, one.department), ("Бухгалтер", "Бухгалтерия"))
		self.assertEqual(one.organization, "ООО «Ромашка»")  # the company of the department
		self.assertEqual(
			(one.office, one.level, one.services), ("Москва, Ленина 1", "Рядовой сотрудник", "Почта, 1С")
		)
		self.assertEqual((one.remote, one.equipment_note), (1, "Ноутбук, 2 монитора"))
		two = frappe.get_doc("B24 Smart Item", f"{self.process.name}:2")
		self.assertEqual(two.organization, "ООО «Ромашка Сервис»")  # the item's own company wins
		self.assertFalse(frappe.db.get_value("B24 Smart Item", f"{self.process.name}:3", "position"))

	def test_snapshot_and_purchase_plan(self):
		# the typical kit of a position comes from the people working in it
		log = self.sync()  # Snipe-IT: who has what
		self.assertEqual(log.status, "Успех", log.messages)
		rows = frappe.db.sql(
			"""select pos.title from `tabIT Asset` a
				join `tabEmployment` e on e.person = a.person and e.status = 'Работает'
				join `tabHR Position` pos on pos.name = e.position
			where a.category = 'Ноутбуки' and a.status_type = 'deployable' limit 1"""
		)
		self.assertTrue(rows, "the fixture has a working employee with a notebook")
		position = rows[0][0]
		CATALOG[1086]["9"] = position
		try:
			self.sync_hiring(
				[
					item(1, "NEW", start=add_days(today(), 10)),  # notebook + 2 monitors by the request
					item(2, "UC_A", position="9", equip=""),  # nothing written: the kit of the position
					item(3, "UC_A", position="6", equip="уточним позже"),  # nothing to go by
					item(4, "UC_C"),  # being hired: not planned
					item(5, "SUCCESS", days=20),
				]
			)
		finally:
			CATALOG[1086].pop("9")
		d = hiring.snapshot()
		k = d["kpis"]
		self.assertEqual((k["recruiting"], k["onboarding"], k["undetermined"]), (3, 1, 1))
		self.assertEqual((k["start_soon"], k["remote"]), (1, 1))
		plan = {r["item_id"]: r for r in d["plan"]}
		self.assertEqual(plan[1]["source"], "по заявке")
		self.assertIn("Мониторы × 2", plan[1]["kit"])
		self.assertEqual(plan[2]["source"], "по должности")
		self.assertIn("Ноутбуки", plan[2]["kit"])
		self.assertEqual(plan[3]["source"], "не определено")
		eq = {r["category"]: r for r in d["equipment"]}
		self.assertEqual((eq["Ноутбуки"]["request"], eq["Ноутбуки"]["position"]), (1, 1))
		self.assertEqual(eq["Ноутбуки"]["stock"], 1)  # NB-0105: deployable, not handed out
		self.assertEqual(eq["Ноутбуки"]["buy"], 1)
		self.assertEqual((eq["Мониторы"]["need"], eq["Мониторы"]["buy"]), (2, 2))  # MN-0104 is in a room
		vac = {(v["position"], v["organization"]): v for v in d["vacancies"]}
		self.assertEqual(vac[("Бухгалтер", "ООО «Ромашка»")]["count"], 1)
		self.assertEqual([f["stage"] for f in d["funnel"] if f["hired"]], ["Оформлен", "Испытательный срок"])
		self.assertEqual({s["service"] for s in d["services"]}, {"Почта", "1С"})

	def test_section_and_separation(self):
		self.sync_hiring([item(1, "NEW")])
		from access_registry.bitrix24 import support

		self.assertEqual(support.processes(), [])  # a recruiting process is not a helpdesk
		user = make_user("hiring-user@registry.test")
		frappe.db.delete("Registry Access Profile", {"name": ["in", ["Подбор: нет", "Подбор: да"]]})
		api.save_profile({"profile_name": "Подбор: нет", "sections": {"people": 1}, "members": [user]})
		frappe.set_user(user)
		self.assertRaises(frappe.PermissionError, api.hiring)
		frappe.set_user("Administrator")
		api.save_profile({"profile_name": "Подбор: да", "sections": {"hiring": 1}, "members": [user]})
		frappe.set_user(user)
		self.assertEqual(api.hiring()["kpis"]["recruiting"], 1)
