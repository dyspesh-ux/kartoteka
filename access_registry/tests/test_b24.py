"""Tests of the Bitrix24 layer: REST client, mirror, links to employees, write-back and reports."""

import copy
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.bitrix24 import reports
from access_registry.bitrix24.access import AccessExpander
from access_registry.bitrix24.client import B24Client, B24Error, encode_query, normalize_webhook
from access_registry.bitrix24.sync import parse_access_code, run_portal_sync
from access_registry.sync.departments import ensure_root
from access_registry.sync.engine import run_source_sync
from access_registry.tests.test_catalog import FL, HR_DOCTYPES, S1
from access_registry.tests.test_zup_sync import TODAY, load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PORTAL = "TSTB24"
B24_DOCTYPES = [
	"B24 Write Log",
	"B24 Access Grant",
	"B24 Absence",
	"B24 Workgroup Member",
	"B24 Workgroup",
	"B24 User Department",
	"B24 User",
	"B24 Department",
	"B24 Portal",
]
AD_DOCTYPES = ["AD Account Group", "AD Account", "AD Group", "AD Domain"]


def portal_data() -> dict:
	with open(os.path.join(FIXTURES, "b24", "portal.json"), encoding="utf-8") as fh:
		return json.load(fh)


def user_row(data, b24_id):
	return next(u for u in data["users"] if u["ID"] == str(b24_id))


class FakeClient:
	def __init__(self, fail_for=()):
		self.updates = []
		self.fail_for = set(fail_for)

	def update_user(self, user_id, fields):
		if str(user_id) in self.fail_for:
			raise B24Error("ACCESS_DENIED", "Доступ запрещён", "user.update")
		self.updates.append((str(user_id), fields))
		return True


class FakeResponse:
	def __init__(self, data, status=200):
		self.data = data
		self.status_code = status
		self.text = json.dumps(data)

	def json(self):
		return self.data


class FakeSession:
	"""Answers REST calls from a function (method, params) -> (data, status)."""

	def __init__(self, handler):
		self.handler = handler
		self.requests = []

	def post(self, url, data=None, headers=None, timeout=None, verify=None):
		method = url.rsplit("/", 1)[1][: -len(".json")]
		params = json.loads(data.decode())
		self.requests.append((method, params))
		answer, status = self.handler(method, params)
		return FakeResponse(answer, status)


class TestB24Client(FrappeTestCase):
	def client(self, handler):
		return B24Client(
			"https://b24.example.local/rest/1/secret/user.get.json",
			session=FakeSession(handler),
			min_interval=0,
			sleep=lambda _s: None,
		)

	def test_webhook_and_query(self):
		self.assertEqual(
			normalize_webhook("https://b24.example.local/rest/1/abc/profile.json"),
			"https://b24.example.local/rest/1/abc/",
		)
		self.assertRaises(ValueError, normalize_webhook, "https://b24.example.local/")
		self.assertEqual(
			encode_query({"FILTER": {">ID": 5, "ACTIVE": True}, "SELECT": ["ID", "NAME"], "start": 50}),
			"FILTER[%3EID]=5&FILTER[ACTIVE]=Y&SELECT[0]=ID&SELECT[1]=NAME&start=50",
		)

	def test_retry_and_errors(self):
		calls = []

		def handler(method, params):
			calls.append(method)
			if len(calls) == 1:
				return {"error": "QUERY_LIMIT_EXCEEDED", "error_description": "Too many requests"}, 503
			if method == "profile":
				return {"result": {"ID": "1"}}, 200
			return {"error": "insufficient_scope", "error_description": "no scope"}, 401

		client = self.client(handler)
		self.assertEqual(client.result("profile"), {"ID": "1"})
		with self.assertRaises(B24Error) as ctx:
			client.call("crm.type.list")
		self.assertEqual(ctx.exception.code, "insufficient_scope")
		self.assertTrue(ctx.exception.is_access)

	def test_list_all_uses_batch(self):
		rows = [{"ID": str(i)} for i in range(1, 121)]

		def handler(method, params):
			if method == "batch":
				result = {}
				for key, command in params["cmd"].items():
					start = int(command.split("start=")[1])
					result[key] = rows[start : start + 50]
				return {"result": {"result": result, "result_error": [], "result_total": {}}}, 200
			start = params["start"]
			return {"result": rows[start : start + 50], "next": 50, "total": len(rows)}, 200

		client = self.client(handler)
		self.assertEqual(
			[r["ID"] for r in client.list_all("user.get", {"FILTER": {"ACTIVE": True}})],
			[str(i) for i in range(1, 121)],
		)
		self.assertEqual([m for m, _p in client.session.requests], ["user.get", "batch"])
		self.assertEqual(sorted(client.session.requests[1][1]["cmd"]), ["p100", "p50"])

	def test_batch_errors_per_command(self):
		def handler(method, params):
			return {
				"result": {
					"result": {"g1": [{"USER_ID": "1", "ROLE": "A"}]},
					"result_error": {"g2": {"error": "ACCESS_DENIED", "error_description": "closed"}},
				}
			}, 200

		members = self.client(handler).workgroup_members(["1", "2"])
		self.assertEqual(members, {"1": [{"USER_ID": "1", "ROLE": "A"}], "2": []})

	def test_access_codes(self):
		self.assertEqual(parse_access_code("SG10_K"), ("SG", "10", "K"))
		self.assertEqual(parse_access_code("DR3"), ("DR", "3", ""))
		self.assertEqual(parse_access_code("AU"), ("AU", "", ""))
		self.assertEqual(parse_access_code("CR5"), ("", "", ""))


class TestBitrix24(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in B24_DOCTYPES + AD_DOCTYPES + HR_DOCTYPES:
			frappe.db.delete(doctype)
		frappe.db.delete("Version", {"ref_doctype": ["like", "B24 %"]})
		ensure_root()
		frappe.get_doc(
			{"doctype": "Info Base", "source_code": S1, "title": S1, "base_url": "http://127.0.0.1:9/hs"}
		).insert()
		frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": PORTAL,
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
				"exporter_url": "https://b24.example.local/local/registry/export.php",
			}
		).insert()
		log = run_source_sync(S1, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Успех", log.messages)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def sync(self, data=None, client=None):
		payload = copy.deepcopy(data or portal_data())
		return run_portal_sync(PORTAL, commit=False, fetch=lambda _p: payload, client=client or FakeClient())

	def assertSuccess(self, log):
		self.assertEqual(log.status, "Успех", log.messages)
		return json.loads(log.stats)

	def user(self, b24_id):
		return frappe.get_doc("B24 User", f"{PORTAL}:{b24_id}")

	def person(self, n):
		return frappe.db.get_value("Person Source ID", {"source": S1, "person_guid": FL(n)}, "parent")

	def enable_writes(self, **values):
		portal = frappe.get_doc("B24 Portal", PORTAL)
		portal.update({"write_enabled": 1, **values})
		portal.save()

	# ---------------------------------------------------------------- import

	def test_import(self):
		stats = self.assertSuccess(self.sync())
		self.assertEqual((stats["users"], stats["departments"], stats["workgroups"]), (8, 5, 2))
		self.assertEqual(frappe.db.get_value("B24 Portal", PORTAL, "portal_url"), "https://b24.example.local")

		ivanov = self.user(1)
		self.assertEqual((ivanov.person, ivanov.person_link_method), (self.person(1), "ФИО"))
		self.assertEqual((ivanov.login, ivanov.is_admin, ivanov.active), ("Ivanov", 1, 1))
		self.assertEqual(str(ivanov.birthday), "1980-01-15")
		self.assertEqual([d.department_name for d in ivanov.departments], ["Дирекция"])
		petrova = self.user(2)
		self.assertEqual((petrova.person, petrova.person_link_method), (self.person(2), "Фамилия и имя"))
		self.assertEqual(self.user(5).person, self.person(6))  # Орлова Елена, без отчества
		smirnov = self.user(7)
		self.assertFalse(smirnov.person)
		self.assertIn("не найден", smirnov.person_link_note)
		self.assertEqual((self.user(6).user_type, self.user(8).active), ("extranet", 0))

		direction = frappe.get_doc("B24 Department", f"{PORTAL}:1")
		self.assertEqual(direction.head, ivanov.name)
		self.assertEqual(direction.head_person, self.person(1))
		self.assertEqual(direction.hr_link_method, "Название")
		self.assertTrue(direction.hr_department)
		self.assertEqual(
			frappe.db.get_value("B24 Department", f"{PORTAL}:2", "parent_department"), direction.name
		)
		marketing = frappe.get_doc("B24 Department", f"{PORTAL}:5")
		self.assertFalse(marketing.hr_department)
		self.assertIn("нет подразделения", marketing.hr_link_note)
		self.assertEqual(frappe.db.get_value("B24 Department", f"{PORTAL}:4", "member_count"), 2)

		project = frappe.get_doc("B24 Workgroup", f"{PORTAL}:10")
		self.assertEqual((project.is_project, project.member_count, project.owner_user), (1, 3, ivanov.name))
		self.assertEqual({m.role for m in project.members}, {"Владелец", "Участник"})

		absence = frappe.get_doc("B24 Absence", f"{PORTAL}:abs:100")
		self.assertEqual((absence.person, str(absence.date_from)), (self.person(3), "2026-06-10"))

		grants = frappe.get_all(
			"B24 Access Grant", fields=["resource_type", "resource", "permission", "via", "principal"]
		)
		deals = next(g for g in grants if g.resource == "Сделки" and g.principal.startswith("Подразделение"))
		self.assertEqual(deals.permission, "Чтение: все; Изменение: свои")
		self.assertEqual(deals.principal, "Подразделение «Отдел продаж» с подотделами")
		self.assertIn("Закупки", [g.resource for g in grants if g.resource_type == "Смарт-процесс"])
		self.assertIn("Администраторы портала", [g.resource for g in grants])
		self.assertNotIn("Группа «Сотрудники»", [g.resource for g in grants])  # EMPLOYEES_* is skipped
		self.assertIn("B24 User", [link.link_doctype for link in frappe.get_meta("Person").links])

	def test_repeat_sync_changes_nothing(self):
		self.sync()
		versions = frappe.db.count("Version", {"ref_doctype": ["like", "B24 %"]})
		stats = self.assertSuccess(self.sync())
		self.assertFalse([k for k in stats if k.endswith(": изменено") or k.endswith(": создано")], stats)
		# the last login changes all the time, no version for it
		data = portal_data()
		user_row(data, 1)["LAST_LOGIN"] = "2026-06-01T08:00:00+03:00"
		self.assertSuccess(self.sync(data))
		self.assertEqual(frappe.db.count("Version", {"ref_doctype": ["like", "B24 %"]}), versions)
		self.assertEqual(str(self.user(1).last_login), "2026-06-01 08:00:00")

	def test_missing_manual_and_guard(self):
		self.sync()
		data = portal_data()
		data["users"] = [u for u in data["users"] if u["ID"] != "7"]
		self.assertSuccess(self.sync(data))
		self.assertEqual(self.user(7).missing_in_source, 1)

		smirnov = self.user(7)
		smirnov.manual_person = self.person(5)
		smirnov.save()
		self.assertSuccess(self.sync())
		smirnov.reload()
		self.assertEqual(
			(smirnov.person, smirnov.person_link_method, smirnov.missing_in_source),
			(self.person(5), "Вручную", 0),
		)

		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 5)
		data = portal_data()
		data["users"] = data["users"][:2]
		self.assertEqual(self.sync(data).status, "Остановлен предохранителем")

	def test_link_through_ad(self):
		from access_registry.active_directory.sync import run_domain_sync
		from access_registry.tests.test_ad import directory

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
		ad = directory()
		log = run_domain_sync("TSTAD", commit=False, fetch=lambda _d: ad)
		self.assertEqual(log.status, "Успех", log.messages)
		self.sync()
		ivanov = self.user(1)
		self.assertEqual(
			(ivanov.person_link_method, ivanov.ad_account),
			("Учётка AD", "TSTAD:00000000-0000-4000-ad00-000000000001"),
		)

	# ---------------------------------------------------------------- write-back

	def test_write_back_fills_only_empty(self):
		self.enable_writes()
		client = FakeClient()
		stats = self.assertSuccess(self.sync(client=client))
		updates = dict(client.updates)
		self.assertEqual(updates["2"], {"SECOND_NAME": "Сергеевна", "PERSONAL_BIRTHDAY": "1985-03-10"})
		self.assertEqual(updates["5"]["SECOND_NAME"], "Павловна")
		self.assertNotIn("1", updates)  # nothing to add
		self.assertNotIn("3", updates)  # birthday differs, but the mode fills only empty values
		self.assertNotIn("8", updates)  # inactive
		self.assertEqual(stats["b24_writes"], sum(len(f) for _u, f in client.updates))
		petrova = self.user(2)
		self.assertEqual((petrova.second_name, str(petrova.birthday)), ("Сергеевна", "1985-03-10"))
		logged = frappe.get_all(
			"B24 Write Log", filters={"b24_user": petrova.name}, fields=["field", "status", "new_value"]
		)
		self.assertEqual(
			{(r.field, r.status) for r in logged},
			{("SECOND_NAME", "Записано"), ("PERSONAL_BIRTHDAY", "Записано")},
		)

		# Bitrix24 now returns the written values: the next run writes nothing
		data = portal_data()
		for b24_id, fields in client.updates:
			row = user_row(data, b24_id)
			row["SECOND_NAME"] = fields.get("SECOND_NAME", row["SECOND_NAME"])
			if "PERSONAL_BIRTHDAY" in fields:
				row["PERSONAL_BIRTHDAY"] = fields["PERSONAL_BIRTHDAY"] + "T03:00:00+03:00"
		again = FakeClient()
		self.assertSuccess(self.sync(data, client=again))
		self.assertEqual(again.updates, [])

	def test_write_back_overwrite_uuid_limit_and_errors(self):
		self.enable_writes(write_mode="Пустые и отличающиеся", person_uuid_field="uf_usr_registry_id")
		self.assertEqual(frappe.db.get_value("B24 Portal", PORTAL, "person_uuid_field"), "UF_USR_REGISTRY_ID")
		client = FakeClient(fail_for={"4"})
		stats = self.assertSuccess(self.sync(client=client))
		updates = dict(client.updates)
		self.assertEqual(updates["3"]["PERSONAL_BIRTHDAY"], "1990-07-20")
		self.assertEqual(updates["1"], {"UF_USR_REGISTRY_ID": self.person(1)})
		self.assertEqual(stats["b24_write_errors"], 1)
		self.assertEqual(
			frappe.db.get_value(
				"B24 Write Log", {"b24_user": f"{PORTAL}:4", "field": "UF_USR_REGISTRY_ID"}, "status"
			),
			"Ошибка",
		)
		# UUID in the profile is the strongest link
		data = portal_data()
		user_row(data, 7)["UF_USR_REGISTRY_ID"] = self.person(5)
		self.sync(data, client=FakeClient())
		self.assertEqual(
			(self.user(7).person, self.user(7).person_link_method), (self.person(5), "UUID в Битрикс24")
		)

		frappe.db.set_value("B24 Portal", PORTAL, "max_writes", 1)
		frappe.db.set_value("B24 User", f"{PORTAL}:2", "second_name", "")
		frappe.db.set_value("B24 User", f"{PORTAL}:5", "second_name", "")
		limited = FakeClient()
		log = self.sync(client=limited)
		self.assertEqual(limited.updates, [])
		self.assertIn("больше предела", log.messages)

	def test_no_writes_when_disabled(self):
		client = FakeClient()
		stats = self.assertSuccess(self.sync(client=client))
		self.assertEqual(client.updates, [])
		self.assertNotIn("b24_writes", stats)

	# ---------------------------------------------------------------- reports

	def test_reports(self):
		self.sync()
		names = lambda rows, key="user": sorted(r[key] for r in rows)  # noqa: E731
		self.assertEqual(names(reports.active_not_working()[1]), [f"{PORTAL}:4"])
		self.assertEqual(names(reports.without_employee()[1]), [f"{PORTAL}:7"])

		diffs = reports.profile_differences()[1]
		self.assertIn(
			("Отчество", "", "Сергеевна"),
			[(r["field"], r["b24_value"], r["hr_value"]) for r in diffs if r["user"] == f"{PORTAL}:2"],
		)
		self.assertIn("Дата рождения", [r["field"] for r in diffs if r["user"] == f"{PORTAL}:3"])
		self.assertEqual(reports.profile_differences({"field": "Отчество"})[1][0]["field"], "Отчество")

		heads = {r.department_name: r.status for r in reports.department_heads({"only_differences": 0})[1]}
		self.assertEqual(heads["Дирекция"], "совпадает")
		self.assertEqual(heads["Бухгалтерия"], "в кадрах не указан")
		self.assertEqual(heads["Маркетинг"], "не сопоставлено с кадрами")
		self.assertNotIn(
			"Дирекция", [r.department_name for r in reports.department_heads({"only_differences": 1})[1]]
		)

		structure = reports.structure()[1]
		self.assertIn(
			("Есть в Битрикс24, нет в кадрах", "Маркетинг"), [(r["side"], r["title"]) for r in structure]
		)
		self.assertIn(
			"Смена А", [r["title"] for r in structure if r["side"] == "Есть в кадрах, нет в Битрикс24"]
		)

		absences = reports.absence_differences({"days_back": 2000, "days_ahead": 2000})[1]
		statuses = {(r["full_name"], r["status"]) for r in absences}
		self.assertIn(("Петрова Мария Сергеевна", "Нет в Битрикс24"), statuses)
		self.assertIn(("Иванов Иван Иванович", "Нет в ЗУП"), statuses)
		self.assertNotIn("Сидоров Пётр Алексеевич", {r["full_name"] for r in absences})

		members = reports.workgroup_members({"only_not_working": 1})[1]
		self.assertEqual(
			[(r.group_name, r.user_name) for r in members],
			[("Проект «Внедрение»", "Фёдоров Фёдор Фёдорович")],
		)
		for report in frappe.get_all("Report", filters={"module": "Bitrix24"}, pluck="name"):
			frappe.get_doc("Report", report).execute_script_report({})

	def test_section_access_expansion(self):
		self.sync()
		expander = AccessExpander(PORTAL)
		self.assertEqual(expander.expand("DR1"), ["1", "2", "3", "4", "5", "7", "8"])
		self.assertEqual(expander.expand("DR3"), ["3", "8"])
		self.assertEqual(expander.expand("SG10_A"), ["1"])
		self.assertEqual(expander.expand("SG10_K"), ["1", "2", "4"])
		self.assertEqual(expander.expand("G12"), ["2"])
		self.assertNotIn("6", expander.expand("AU"))  # extranet is not an employee

		rows = reports.section_access({"resource_type": "CRM"})[1]
		deal_users = {r["user_name"] for r in rows if r["resource"] == "Сделки"}
		self.assertEqual(
			deal_users, {"Сидоров Пётр Алексеевич", "Фёдоров Фёдор Фёдорович"}
		)  # Кузнецова inactive
		config = [r for r in rows if r["resource"] == "Настройки CRM"]
		self.assertEqual([r["user_name"] for r in config], ["Петрова Мария"])
		not_working = reports.section_access({"only_not_working": 1})[1]
		self.assertTrue(not_working)
		self.assertEqual({r["user_name"] for r in not_working}, {"Фёдоров Фёдор Фёдорович"})
		disk = reports.section_access({"resource": "Бухгалтерия"})[1]
		self.assertEqual(
			[(r["user_name"], r["permission"]) for r in disk], [("Петрова Мария", "Изменение")]
		)
