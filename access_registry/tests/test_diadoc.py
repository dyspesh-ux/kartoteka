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


class TestDiadocLinking(FrappeTestCase):
	# Names are unique on purpose: the dev site holds a large synthetic HR dataset,
	# a common name would match several persons and stay ambiguous.
	# One transaction per test class: the person is created once in setUpClass and
	# rolled back with the class cleanup, so tests of the class see each other's data.
	FIO = "Тестдиадок Иван Иванович"
	FIO_CAPS = "ТЕСТДИАДОК ИВАН ИВАНОВИЧ"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.person = frappe.get_doc(
			{
				"doctype": "Person",
				"last_name": "Тестдиадок",
				"first_name": "Иван",
				"middle_name": "Иванович",
				"status": "Работает",
			}
		).insert(ignore_permissions=True)

	def test_resolve_by_ad_mail(self):
		from access_registry.diadoc.linking import BY_EMAIL_AD, DiadocResolver

		frappe.get_doc(
			{
				"doctype": "AD Account",
				"uid": "TST:acc-1",
				"sam_account_name": "iivanov",
				"user_principal_name": "iivanov@corp.example",
				"person": self.person.name,
			}
		).insert(ignore_permissions=True)
		person, method, note = DiadocResolver().resolve("Сидоров Сидор Сидорович", "iivanov@corp.example")
		self.assertEqual(person, self.person.name)
		self.assertEqual(method, BY_EMAIL_AD)
		self.assertEqual(note, "")

	def test_resolve_by_b24_mail(self):
		from access_registry.diadoc.linking import BY_EMAIL_B24, DiadocResolver

		other = frappe.get_doc(
			{"doctype": "Person", "last_name": "Тестдиадокпетров", "first_name": "Пётр"}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "B24 User",
				"uid": "portal:1",
				"full_name": "Тестдиадокпетров Пётр",
				"email": "ppetrov-testdiadok@corp.example",
				"active": 1,
				"person": other.name,
			}
		).insert(ignore_permissions=True)
		person, method, _note = DiadocResolver().resolve("Сидоров Сидор", "ppetrov-testdiadok@corp.example")
		self.assertEqual(person, other.name)
		self.assertEqual(method, BY_EMAIL_B24)

	def test_resolve_by_full_name(self):
		from access_registry.diadoc.linking import BY_NAME, DiadocResolver

		person, method, _note = DiadocResolver().resolve(self.FIO, "")
		self.assertEqual(person, self.person.name)
		self.assertEqual(method, BY_NAME)

	def test_resolve_caps_full_name(self):
		from access_registry.diadoc.linking import BY_NAME, DiadocResolver

		person, method, _note = DiadocResolver().resolve(self.FIO_CAPS, "")
		self.assertEqual((person, method), (self.person.name, BY_NAME))

	def test_resolve_not_found(self):
		from access_registry.diadoc.linking import DiadocResolver

		person, method, note = DiadocResolver().resolve("Пупкин Иннокентий", "nobody@nowhere.example")
		self.assertIsNone(person)
		self.assertEqual(method, "")
		self.assertIn("не найден", note)


class TestDiadocUpload(FrappeTestCase):
	"""The upload mirror. One transaction per class: tests use their own organization names."""

	@staticmethod
	def file_for(org, users):
		return export_bytes([make_row(org=org, **u) for u in users])

	def test_first_load_creates_orgs_and_users(self):
		from access_registry.diadoc.sync import run_upload

		data = self.file_for(
			"Т-Синк-1",
			[
				{
					"name": "Тестдиадоксинк1 Андрей Андреевич",
					"login": "a.a@test-sync.example",
					"can_sign": True,
				},
				{"name": "Тестдиадоксинк1 Борис Борисович", "login": ""},
			],
		)
		log = run_upload(data, commit=False)
		self.assertEqual(log.status, "Успех")
		self.assertEqual(log.kind, "Диадок")
		self.assertTrue(frappe.db.exists("Diadoc Organization", "Т-Синк-1"))
		self.assertEqual(frappe.db.count("Diadoc User", {"organization": "Т-Синк-1"}), 2)
		self.assertTrue(frappe.db.get_value("Diadoc Organization", "Т-Синк-1", "last_upload"))
		no_login = frappe.db.get_value(
			"Diadoc User",
			{"organization": "Т-Синк-1", "full_name": "Тестдиадоксинк1 Борис Борисович"},
			["name", "login"],
			as_dict=True,
		)
		self.assertTrue(no_login.name.startswith("Т-Синк-1:фио:"))
		self.assertEqual(no_login.login, "")

	def test_guard_trips_on_shrunk_file(self):
		from access_registry.diadoc.sync import run_upload

		org = "Т-Синк-Г"
		run_upload(
			self.file_for(
				org,
				[{"name": f"Тестдиадокг{i} Иван Иванович", "login": f"g{i}@test.example"} for i in range(4)],
			),
			commit=False,
		)
		run_upload(
			self.file_for(
				org,
				[{"name": f"Тестдиадокг{i} Иван Иванович", "login": f"g{i}@test.example"} for i in range(5)],
			),
			commit=False,
		)
		log = run_upload(
			self.file_for(org, [{"name": "Тестдиадокг0 Иван Иванович", "login": "g0@test.example"}]),
			commit=False,
		)
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertEqual(frappe.db.count("Diadoc User", {"organization": org, "missing_in_source": 0}), 5)

	def test_manual_person_survives_reload(self):
		from access_registry.diadoc.sync import run_upload

		org = "Т-Синк-М"
		row = {"name": "Тестдиадокм Мануал Мануалович", "login": "manual@test-sync.example"}
		run_upload(self.file_for(org, [row]), commit=False)
		user_name = frappe.db.get_value("Diadoc User", {"organization": org}, "name")
		manual = frappe.get_doc(
			{"doctype": "Person", "last_name": "Тестдиадокручной", "first_name": "Мария"}
		).insert(ignore_permissions=True)
		user = frappe.get_doc("Diadoc User", user_name)
		user.manual_person = manual.name
		user.save(ignore_permissions=True)
		run_upload(self.file_for(org, [row]), commit=False)
		reloaded = frappe.get_doc("Diadoc User", user_name)
		self.assertEqual(reloaded.person, manual.name)
		self.assertEqual(reloaded.person_link_method, "Вручную")

	def test_missing_marked_only_for_orgs_in_file(self):
		from access_registry.diadoc.sync import run_upload

		run_upload(
			export_bytes(
				[
					make_row(org="Т-Синк-А", name="Тестдиадока Первый Первович", login="a1@test.example"),
					make_row(org="Т-Синк-А", name="Тестдиадока Второй Вторович", login="a2@test.example"),
					make_row(org="Т-Синк-Б", name="Тестдиадокб Третий Третович", login="b1@test.example"),
				]
			),
			commit=False,
		)
		run_upload(
			self.file_for("Т-Синк-А", [{"name": "Тестдиадока Первый Первович", "login": "a1@test.example"}]),
			commit=False,
		)
		gone = frappe.db.get_value(
			"Diadoc User", {"organization": "Т-Синк-А", "login": "a2@test.example"}, "missing_in_source"
		)
		kept = frappe.db.get_value(
			"Diadoc User", {"organization": "Т-Синк-Б", "login": "b1@test.example"}, "missing_in_source"
		)
		self.assertEqual(gone, 1)
		self.assertEqual(kept, 0)

	def test_person_linking_applied_and_log_has_no_names(self):
		from access_registry.diadoc.linking import BY_EMAIL_AD
		from access_registry.diadoc.sync import run_upload

		person = frappe.get_doc(
			{
				"doctype": "Person",
				"last_name": "Тестдиадоклинк",
				"first_name": "Лев",
				"middle_name": "Львович",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "AD Account",
				"uid": "TST:acc-diadoc-link",
				"sam_account_name": "linkdiadoc",
				"user_principal_name": "linkdiadoc@test-sync.example",
				"person": person.name,
			}
		).insert(ignore_permissions=True)
		log = run_upload(
			self.file_for(
				"Т-Синк-Л", [{"name": "Тестдиадоклинк Лев Львович", "login": "linkdiadoc@test-sync.example"}]
			),
			commit=False,
		)
		user = frappe.db.get_value(
			"Diadoc User", {"organization": "Т-Синк-Л"}, ["person", "person_link_method"], as_dict=True
		)
		self.assertEqual((user.person, user.person_link_method), (person.name, BY_EMAIL_AD))
		self.assertNotIn("Тестдиадоклинк", log.messages or "")

	def test_reload_unchanged_creates_no_versions(self):
		import json as jsonlib

		from access_registry.diadoc.sync import run_upload

		org = "Т-Синк-В"
		data = self.file_for(
			org,
			[
				{"name": "Тестдиадокв Один Одинович", "login": "v1@test.example", "can_sign": True},
				{"name": "Тестдиадокв Два Дваович", "login": "v2@test.example"},
			],
		)
		run_upload(data, commit=False)
		versions_before = frappe.db.count("Version", {"ref_doctype": "Diadoc User"})
		log = run_upload(data, commit=False)
		self.assertEqual(frappe.db.count("Version", {"ref_doctype": "Diadoc User"}), versions_before)
		stats = jsonlib.loads(log.stats)
		self.assertEqual(stats["Diadoc User"]["unchanged"], 2)


class TestDiadocUploadPermissions(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from access_registry.tests.test_permissions import make_user

		cls.specialist = make_user("diadoc-specialist@registry.test", "Diadoc Specialist")
		cls.plain = make_user("diadoc-plain@registry.test")
		cls.data = export_bytes(
			[
				make_row(
					org="Т-Кнопка",
					name="Тестдиадоккнопка Иван Иванович",
					login="btn@test-sync.example",
					can_sign=True,
				)
			]
		)
		filedoc = frappe.get_doc(
			{"doctype": "File", "file_name": "diadoc-btn-test.xlsx", "content": cls.data}
		).insert(ignore_permissions=True)
		cls.file_url = filedoc.file_url
		frappe.get_doc({"doctype": "Diadoc Organization", "title": "Т-Кнопка"}).insert(
			ignore_permissions=True
		)

	def test_load_file_requires_role(self):
		org = frappe.get_doc("Diadoc Organization", "Т-Кнопка")
		org.upload_file = self.file_url
		frappe.set_user(self.plain)
		try:
			with self.assertRaises(frappe.PermissionError):
				org.load_file()
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(self.specialist)
		try:
			result = org.load_file()
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(result["status"], "Успех")
		self.assertTrue(frappe.db.exists("Diadoc User", {"organization": "Т-Кнопка"}))

	def test_load_file_without_attachment_throws(self):
		org = frappe.get_doc("Diadoc Organization", "Т-Кнопка")
		with self.assertRaises(frappe.ValidationError):
			org.load_file()
