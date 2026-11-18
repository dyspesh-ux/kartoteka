"""Tests of the Diadoc module: scaffold, parser, person linking, upload sync, entitlements, reminders."""

import io

import frappe
import openpyxl
from frappe.tests.utils import FrappeTestCase

HEADERS = [
	"Компания",
	"ФИО",
	"Должность",
	"Логин",
	"Логин акт.",
	"Админ",
	"Заблок.",
	"Подпись",
	"Госключ",
	"Согл.",
	"Запрос согл.",
	"Черновики",
	"Удаление",
	"Платежки",
	"Контрагенты",
]


def export_bytes(rows, empty_first_row=True, legend=False):
	"""xlsx of the «Диадок Общий Реестр» shape: data in columns B..P, legend in R..S."""
	wb = openpyxl.Workbook()
	ws = wb.active
	header_row = 1
	if empty_first_row:
		ws.append([None] * 19)
		header_row = 2
	ws.append([None, *HEADERS])
	for row in rows:
		ws.append([None, *row])
	if legend:
		ws.cell(row=header_row + 1, column=18, value="Логин акт.")
		ws.cell(row=header_row + 1, column=19, value="УЗ Активна")
	buf = io.BytesIO()
	wb.save(buf)
	return buf.getvalue()


def make_row(
	org="А-1650", name="Иванов Иван Иванович", position="Бухгалтер", login="ivanov.ii@x.ru", **flags
):
	row = [org, name, position, login]
	for flag in (
		"login_active",
		"is_admin",
		"blocked",
		"can_sign",
		"gov_key",
		"can_approve",
		"can_request_approve",
		"drafts",
		"can_delete",
		"payments",
		"counterparties",
	):
		row.append("True" if flags.get(flag) else "False")
	return row


class TestDiadocScaffold(FrappeTestCase):
	def test_module_scaffold(self):
		self.assertTrue(frappe.db.exists("DocType", "Diadoc Organization"))
		self.assertTrue(frappe.db.exists("DocType", "Diadoc User"))
		self.assertTrue(frappe.db.exists("Role", "Diadoc Specialist"))
		ws = frappe.get_doc("Workspace", "Диадок")
		linked = {row.link_to for row in ws.links if row.link_type == "DocType"}
		self.assertIn("Diadoc Organization", linked)
		self.assertIn("Diadoc User", linked)


class TestDiadocParser(FrappeTestCase):
	def test_parse_finds_header_not_in_first_row(self):
		from access_registry.diadoc.parser import parse

		parsed = parse(export_bytes([make_row()]))
		self.assertEqual(parsed["orgs"], ["А-1650"])
		self.assertEqual(len(parsed["users"]), 1)
		user = parsed["users"][0]
		self.assertEqual(user["full_name"], "Иванов Иван Иванович")
		self.assertEqual(user["login"], "ivanov.ii@x.ru")
		self.assertEqual(user["position"], "Бухгалтер")

	def test_parse_booleans_and_legend_ignored(self):
		from access_registry.diadoc.parser import parse

		parsed = parse(
			export_bytes(
				[make_row(login_active=True, can_sign=True, counterparties=True)],
				legend=True,
			)
		)
		rights = parsed["users"][0]["rights"]
		self.assertEqual(rights["login_active"], 1)
		self.assertEqual(rights["can_sign"], 1)
		self.assertEqual(rights["counterparties"], 1)
		self.assertEqual(rights["is_admin"], 0)
		self.assertEqual(rights["blocked"], 0)

	def test_parse_bool_accepts_variants(self):
		from access_registry.diadoc.parser import parse

		row = make_row(login_active=True)
		row[4] = "истина"
		parsed = parse(export_bytes([row]))
		self.assertEqual(parsed["users"][0]["rights"]["login_active"], 1)

	def test_parse_login_dash_becomes_empty(self):
		from access_registry.diadoc.parser import parse

		parsed = parse(export_bytes([make_row(login="—")]))
		self.assertEqual(parsed["users"][0]["login"], "")

	def test_parse_multi_org_and_duplicates(self):
		from access_registry.diadoc.parser import parse

		parsed = parse(
			export_bytes(
				[
					make_row(org="Сенега", name="Петров Петр Петрович", can_sign=True),
					make_row(org="А-1650", can_approve=True),
					make_row(org="А-1650", can_sign=True),
				]
			)
		)
		self.assertEqual(parsed["orgs"], ["Сенега", "А-1650"])
		by_name = {u["full_name"]: u for u in parsed["users"]}
		self.assertEqual(by_name["Иванов Иван Иванович"]["rights"]["can_sign"], 1)
		self.assertEqual(by_name["Иванов Иван Иванович"]["rights"]["can_approve"], 0)
		self.assertTrue(any("А-1650" in w for w in parsed["warnings"]))

	def test_parse_bad_file_raises(self):
		from access_registry.diadoc.parser import ParseError, parse

		wb = openpyxl.Workbook()
		ws = wb.active
		ws.append(["Компания", "Логин"])
		ws.append(["А-1650", "x@y.ru"])
		buf = io.BytesIO()
		wb.save(buf)
		with self.assertRaises(ParseError):
			parse(buf.getvalue())
