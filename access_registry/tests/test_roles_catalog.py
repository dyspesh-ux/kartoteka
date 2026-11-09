"""The catalog filled from the mirrored systems and the role model import from Excel."""

import frappe

from access_registry.access_roles import catalog, xlsx_io
from access_registry.tests.test_catalog import S1
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_roles import RoleFixture
from access_registry.tests.test_zup_sync import FL, person_of

PRAVA = [
	"Название*",
	"Система*",
	"Риск",
	"Привилегированное",
	"Владелец",
	"Что даёт",
	"AD-группа",
	"Профиль 1С",
	"Группа Б24",
]
ROLES = ["Роль*", "Вид", "Описание", "Статус", "Права", "Обязательные"]


def workbook_bytes(rows_by_sheet: dict) -> bytes:
	from io import BytesIO

	from openpyxl import Workbook

	wb = Workbook()
	first = True
	for title, (header, rows) in rows_by_sheet.items():
		ws = wb.active if first else wb.create_sheet()
		first = False
		ws.title = title
		ws.append(header)
		for row in rows:
			ws.append(row)
	buf = BytesIO()
	wb.save(buf)
	return buf.getvalue()


def make_uncatalogued_profile():
	"""A profile nobody catalogued, given to one employee: a real candidate for the catalog."""
	profile = frappe.get_doc(
		{
			"doctype": "IB Access Profile",
			"uid": f"{S1}:00000000-0000-4000-f300-000000000099",
			"profile_name": "Профиль вне каталога",
			"base_code": S1,
		}
	).insert(ignore_permissions=True)
	user = frappe.get_doc("IB User", {"person": person_of(S1, FL(1))})
	user.append("profiles", {"profile": profile.name})
	user.save(ignore_permissions=True)
	return profile


class TestCatalogCandidates(RoleFixture):
	def test_candidates_list_uncatalogued_rights(self):
		profile = make_uncatalogued_profile()
		rows = catalog.catalog_candidates()
		keys = {r["key"] for r in rows}
		self.assertIn(f"1c:{profile.name}", keys)
		row = next(r for r in rows if r["key"] == f"1c:{profile.name}")
		self.assertEqual((row["system"], row["holders"]), ("1С", 1))
		# the fixture entitlements are catalogued and must not come back as candidates
		catalogued_titles = {
			frappe.db.get_value("Entitlement", name, "title")
			for name in (self.kadr, self.buh, self.empty, self.zup_group, self.buh_read)
		}
		self.assertFalse(catalogued_titles & {r["title"] for r in rows})
		only_ad = catalog.catalog_candidates(system="Active Directory")
		self.assertNotIn(f"1c:{profile.name}", {r["key"] for r in only_ad})

	def test_candidates_need_the_roles_section(self):
		outsider = make_user("catalog-outsider@registry.test")
		frappe.set_user(outsider)
		self.assertRaises(frappe.PermissionError, catalog.catalog_candidates)
		manager = make_user("catalog-manager@registry.test", "Access Role Manager")
		frappe.set_user(manager)
		catalog.catalog_candidates()  # the desk role opens the section

	def test_catalog_add_creates_bound_entitlements(self):
		profile = make_uncatalogued_profile()
		names = catalog.catalog_add([f"1c:{profile.name}"])
		self.assertEqual(len(names), 1)
		doc = frappe.get_doc("Entitlement", names[0])
		self.assertEqual(doc.active, 1)
		self.assertEqual(doc.ib_profile, profile.name)
		# repeated add returns the same card (no duplicates)
		self.assertEqual(catalog.catalog_add([f"1c:{profile.name}"]), names)

	def test_catalog_add_needs_work_level(self):
		viewer = make_user("catalog-add-viewer@registry.test", "Access Catalog Viewer")
		frappe.set_user(viewer)
		# the viewer role reads the section but works nowhere: adding is forbidden
		self.assertRaises(frappe.PermissionError, catalog.catalog_add, ["1c:x"])


class TestRoleModelImport(RoleFixture):
	def import_file(self, prava_rows, role_rows):
		return workbook_bytes({"Права": (PRAVA, prava_rows), "Роли": (ROLES, role_rows)})

	def test_check_then_apply(self):
		make_uncatalogued_profile()
		content = self.import_file(
			[
				[
					"Тест: доступ из файла",
					"1С",
					"Высокий",
					"да",
					None,
					"Проверочное право",
					None,
					"Профиль вне каталога",
					None,
				]
			],
			[
				[
					"Роль из файла",
					"Должностная",
					"Описание",
					"Черновик",
					"Тест: доступ из файла",
					"Тест: доступ из файла",
				]
			],
		)
		imp = xlsx_io.RoleModelImport(content)
		result = imp.run(apply=False)
		self.assertFalse(result["errors"])
		self.assertFalse(result["applied"])
		self.assertEqual(result["counters"], {"прав создано": 1, "ролей создано": 1})
		self.assertFalse(frappe.db.exists("Entitlement", {"title": "Тест: доступ из файла"}))  # nothing saved

		imp = xlsx_io.RoleModelImport(content)
		result = imp.run(apply=True)
		self.assertTrue(result["applied"])
		ent = frappe.get_doc("Entitlement", {"title": "Тест: доступ из файла"})
		self.assertEqual((ent.risk, ent.privileged, ent.system), ("Высокий", 1, "1С"))
		self.assertTrue(ent.ib_profile)
		role = frappe.get_doc("Access Role", "Роль из файла")
		self.assertEqual(len(role.entitlements), 1)
		self.assertEqual(role.entitlements[0].requirement, "Обязательно")

	def test_update_replaces_role_entitlements(self):
		content = self.import_file(
			[
				["Тест: А", "1С", None, None, None, None, None, None, None],
				["Тест: Б", "1С", None, None, None, None, None, None, None],
			],
			[["Роль обновления", "Базовая", None, "Действует", "Тест: А", None]],
		)
		xlsx_io.RoleModelImport(content).run(apply=True)
		content = self.import_file(
			[
				["Тест: А", "1С", None, None, None, None, None, None, None],
				["Тест: Б", "1С", None, None, None, None, None, None, None],
			],
			[["Роль обновления", "Базовая", None, "Действует", "Тест: Б", None]],
		)
		result = xlsx_io.RoleModelImport(content).run(apply=True)
		self.assertEqual(result["counters"], {"прав обновлено": 2, "ролей обновлено": 1})
		role = frappe.get_doc("Access Role", "Роль обновления")
		self.assertEqual(
			[e.entitlement for e in role.entitlements],
			[frappe.db.get_value("Entitlement", {"title": "Тест: Б"})],
		)

	def test_errors_are_listed_and_nothing_is_saved(self):
		content = self.import_file(
			[
				["Тест: дубль", "1С", None, None, None, None, None, None, None],
				["Тест: дубль", "1С", None, None, None, None, None, None, None],
				["Тест: плохая система", "НЛО", None, None, None, None, None, None, None],
				[None, "1С", None, None, None, None, None, None, None],
				[
					"Тест: битая привязка",
					"Active Directory",
					None,
					None,
					None,
					None,
					"Нет такой группы",
					None,
					None,
				],
			],
			[["Роль на несуществующее", "Должностная", None, "Черновик", "Такого права нет", None]],
		)
		result = xlsx_io.RoleModelImport(content).run(apply=True)
		self.assertFalse(result["applied"])
		joined = "\n".join(result["errors"])
		self.assertIn("дубль права", joined)
		self.assertIn("система «НЛО»", joined)
		self.assertIn("не указано название права", joined)
		self.assertIn("не найдено в зеркале", joined)
		self.assertIn("Такого права нет", joined)
		self.assertFalse(frappe.db.exists("Access Role", "Роль на несуществующее"))

	def test_mandatory_must_be_subset_of_entitlements(self):
		content = self.import_file(
			[
				["Тест: В", "1С", None, None, None, None, None, None, None],
				["Тест: Другой", "1С", None, None, None, None, None, None, None],
			],
			[["Роль с обязательным", "Должностная", None, "Черновик", "Тест: В", "Тест: В; Тест: Другой"]],
		)
		result = xlsx_io.RoleModelImport(content).run(apply=True)
		self.assertFalse(result["applied"])
		self.assertIn("обязательные права не входят", "\n".join(result["errors"]))

	def test_template_roundtrip(self):
		frappe.get_doc(
			{
				"doctype": "Access Role",
				"role_name": "Роль для шаблона",
				"kind": "Должностная",
				"status": "Черновик",
				"entitlements": [{"entitlement": self.kadr, "requirement": "Обязательно"}],
			}
		).insert(ignore_permissions=True)
		content = xlsx_io.template_workbook()
		imp = xlsx_io.RoleModelImport(content)
		result = imp.run(apply=False)
		self.assertFalse(result["errors"], result["errors"])
		self.assertIn("прав обновлено", result["counters"])
		self.assertIn("ролей обновлено", result["counters"])
