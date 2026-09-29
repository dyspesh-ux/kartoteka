"""Tests of loading business processes from Excel."""

import io

import frappe

from access_registry.business_processes.xlsx_io import SHEETS, ProcessImport, build_workbook, read_workbook
from access_registry.tests.test_roles import RoleFixture


def workbook(processes=(), roles=(), participants=()) -> bytes:
	from openpyxl import Workbook

	wb = Workbook()
	wb.remove(wb.active)
	for title, rows in (("Процессы", processes), ("Роли", roles), ("Участники", participants)):
		ws = wb.create_sheet(title)
		columns = SHEETS[title]
		ws.append([label for _key, label in columns])
		for row in rows:
			ws.append([row.get(key) for key, _label in columns])
	buffer = io.BytesIO()
	wb.save(buffer)
	return buffer.getvalue()


PROCESSES = [
	{
		"process_code": "ФИН-01",
		"title": "Закрытие месяца",
		"parent": "ФИН",
		"status": "Действует",
		"owner": "Петрова Мария Сергеевна",
		"version": "1.2",
		"effective_from": "2026-01-01",
	},
	{"process_code": "ФИН", "title": "Финансы", "level": "Группа процессов"},
]
ROLES = [
	{
		"process": "ФИН-01",
		"role_name": "Главный бухгалтер",
		"raci": "A",
		"needs_deputy": "да",
		"access_role": "главный бухгалтер",
		"entitlements": "1С: Бухгалтер; AD: папка бухгалтерии",
	},
	{
		"process": "Закрытие месяца",
		"role_name": "Исполнитель",
		"raci": "r",
		"min_participants": 2,
		"needs_deputy": "нет",
	},
]
PARTICIPANTS = [
	{"process": "ФИН-01", "role_name": "Исполнитель", "person": "Сидоров Пётр Алексеевич"},
	{
		"process": "ФИН-01",
		"role_name": "Исполнитель",
		"person": "Иванов Иван Иванович",
		"participation": "Заместитель",
		"valid_to": "2026-12-31",
	},
]


class TestProcessImport(RoleFixture):
	def setUp(self):
		super().setUp()
		self.base_model()

	def load(self, content, apply=True, remove=False):
		return ProcessImport(content, remove).run(apply=apply)

	def test_import(self):
		result = self.load(workbook(PROCESSES, ROLES, PARTICIPANTS))
		self.assertEqual(result["errors"], [])
		self.assertEqual(
			result["counters"],
			{"процессов создано": 2, "ролей создано": 2, "участников добавлено": 2},
		)
		close = frappe.get_doc("Business Process", {"process_code": "ФИН-01"})
		self.assertEqual(
			frappe.db.get_value("Business Process", close.parent_business_process, "process_code"), "ФИН"
		)
		self.assertEqual(
			(close.owner_person, close.version, str(close.effective_from)),
			(self.person(2), "1.2", "2026-01-01"),
		)
		self.assertEqual(frappe.db.get_value("Business Process", {"process_code": "ФИН"}, "is_group"), 1)
		chief = frappe.get_doc("Process Role", {"role_name": "Главный бухгалтер"})
		self.assertEqual(
			(chief.raci, chief.filled_by_access_role, chief.needs_deputy),
			("Отвечает за результат (A)", "Главный бухгалтер", 1),
		)
		self.assertEqual({e.entitlement for e in chief.entitlements}, {self.buh, self.buh_read})
		doer = frappe.get_doc("Process Role", {"role_name": "Исполнитель"})
		self.assertEqual((doer.raci, doer.min_participants, doer.needs_deputy), ("Исполнитель (R)", 2, 0))
		deputy = frappe.get_doc("Process Participant", {"person": self.person(1)})
		self.assertEqual((deputy.participation, str(deputy.valid_to)), ("Заместитель", "2026-12-31"))

		# the same file again changes nothing
		again = self.load(workbook(PROCESSES, ROLES, PARTICIPANTS))
		self.assertEqual((again["errors"], again["counters"]), ([], {}))

		# the exported workbook reads back without changes (round trip)
		self.assertEqual(self.load(build_workbook())["counters"], {})
		sheets = read_workbook(build_workbook())
		self.assertEqual(len(sheets["Участники"]), 2)

	def test_errors_save_nothing(self):
		bad_roles = ROLES + [
			{"process": "НЕТ-ТАКОГО", "role_name": "Кто-то"},
			{
				"process": "ФИН-01",
				"role_name": "Проверяющий",
				"raci": "Z",
				"entitlements": "Несуществующее право",
			},
		]
		bad_people = PARTICIPANTS + [
			{"process": "ФИН-01", "role_name": "Исполнитель", "person": "Пупкин Василий"}
		]
		result = self.load(workbook(PROCESSES, bad_roles, bad_people))
		self.assertFalse(result["applied"])
		text = "\n".join(result["errors"])
		self.assertIn("Лист «Роли», строка 4: процесс «НЕТ-ТАКОГО» не найден", text)
		self.assertIn("участие «Z»", text)
		self.assertIn(
			"Лист «Участники», строка 4: сотрудник: сотрудник с ФИО «Пупкин Василий» не найден", text
		)
		self.assertFalse(frappe.db.exists("Business Process", {"process_code": "ФИН-01"}))

	def test_check_only_and_remove_missing(self):
		dry = self.load(workbook(PROCESSES, ROLES, PARTICIPANTS), apply=False)
		self.assertEqual((dry["errors"], dry["applied"]), ([], False))
		self.assertEqual(dry["counters"]["процессов создано"], 2)
		self.assertFalse(frappe.db.exists("Business Process", {"process_code": "ФИН-01"}))

		self.load(workbook(PROCESSES, ROLES, PARTICIPANTS))
		result = self.load(workbook(PROCESSES, ROLES, PARTICIPANTS[:1]), remove=True)
		self.assertEqual(result["counters"], {"участников удалено": 1})
		self.assertFalse(frappe.db.exists("Process Participant", {"person": self.person(1)}))

	def test_cycle(self):
		rows = [
			{"process_code": "A", "title": "A", "parent": "B"},
			{"process_code": "B", "title": "B", "parent": "A"},
		]
		result = self.load(workbook(rows))
		self.assertEqual(len(result["errors"]), 2)
		self.assertIn("цикл", result["errors"][0])

	def test_form_and_template(self):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "processes.xlsx",
				"content": workbook(PROCESSES, ROLES, PARTICIPANTS),
				"is_private": 1,
			}
		).insert()
		doc = frappe.get_doc({"doctype": "Process Import", "import_file": file.file_url}).insert()
		doc.check()
		self.assertEqual(doc.status, "Проверен")
		doc.load()
		self.assertEqual(doc.status, "Загружен")
		self.assertIn("процессов создано: 2", doc.summary)
		self.assertRaises(frappe.ValidationError, doc.load)

		from access_registry.business_processes.api import download_template

		download_template()
		self.assertTrue(frappe.response["filecontent"].startswith(b"PK"))
		self.assertIn("Процессы", read_workbook(frappe.response["filecontent"]))
