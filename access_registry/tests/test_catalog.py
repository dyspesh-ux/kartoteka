"""Tests of the catalog of 1C users and rights (ITAccess snapshot and event log) for any 1C base."""

import copy
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.access_catalog import api, importer
from access_registry.access_catalog.pull import run_log, run_snapshot
from access_registry.sync.departments import ensure_root
from access_registry.sync.engine import run_source_sync
from access_registry.tests.test_zup_sync import TODAY, load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "itaccess")
S1, S2, BP = "TST1", "TST2", "TSTBP"
CATALOG_DOCTYPES = [
	"IB Audit Event",
	"IB User",
	"IB User Profile",
	"IB User Role",
	"IB Access Profile",
	"IB Profile Role",
]
HR_DOCTYPES = [
	"HR Event",
	"HR Absence",
	"Person Merge Candidate",
	"Employment",
	"Person Source ID",
	"Person",
	"HR Position",
	"HR Department",
	"HR Organization",
	"Legal Entity",
	"Sync Log",
	"Info Base",
]


def g(kind, n):
	return f"00000000-0000-4000-{kind}-{n:012d}"


def USR(n):
	return g("f100", n)


def IB(n):
	return g("f200", n)


def PRF(n):
	return g("f300", n)


def FL(n):
	return g("e000", n)


def snapshot() -> dict:
	with open(os.path.join(FIXTURES, "snapshot_sample.json"), encoding="utf-8") as fh:
		return json.load(fh)


def log_payload() -> dict:
	with open(os.path.join(FIXTURES, "log_sample.json"), encoding="utf-8") as fh:
		return json.load(fh)


def user_rights(data, n):
	return next(r for r in data["user_rights"] if r["user_id"] == USR(n))


class TestAccessCatalog(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in CATALOG_DOCTYPES + HR_DOCTYPES:
			frappe.db.delete(doctype)
		frappe.db.delete("Version", {"ref_doctype": ["like", "IB %"]})
		ensure_root()
		for code in (S1, S2):
			frappe.get_doc(
				{
					"doctype": "Info Base",
					"source_code": code,
					"title": code,
					"base_url": "http://127.0.0.1:9/hs",
				}
			).insert()
		# Accounting base: no HR data, only users and rights
		frappe.get_doc(
			{
				"doctype": "Info Base",
				"source_code": BP,
				"title": "Бухгалтерия",
				"configuration": "Бухгалтерия",
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def imp(self, data=None, base=S1):
		result, warnings = importer.import_snapshot_data(base, copy.deepcopy(data or snapshot()))
		return result

	def versions(self, doctype="IB User", name=None):
		filters = {"ref_doctype": doctype}
		if name:
			filters["docname"] = name
		return frappe.get_all("Version", filters=filters, fields=["name", "data"], order_by="creation")

	# ---------------------------------------------------------------- 2. import

	def test_import_snapshot(self):
		result = self.imp()
		self.assertEqual(result, {"profiles": 5, "users": 6, "orphans": 1, "changed": 12, "missing": 0})

		profile = frappe.get_doc("IB Access Profile", f"{S1}:{PRF(1)}")
		self.assertEqual(profile.profile_name, "Кадровик")
		self.assertEqual(profile.base_code, S1)
		self.assertEqual(profile.supplied, 1)
		self.assertEqual(
			sorted(r.role_name for r in profile.roles),
			["ДобавлениеИзменениеКадровыхДанных", "ЧтениеКадровыхДанных"],
		)

		ivanov = frappe.get_doc("IB User", f"{S1}:{USR(1)}")
		self.assertEqual(ivanov.user_name, "Иванов Иван Иванович")
		self.assertEqual(ivanov.login, "Иванов ИИ")
		self.assertEqual((ivanov.ad_domain, ivanov.ad_login), ("corp", "ivanov"))
		self.assertEqual(ivanov.login_allowed, 1)
		self.assertEqual(ivanov.all_orgs, 0)
		self.assertEqual(ivanov.orgs_text, "Ромашка ООО")
		self.assertEqual(len(ivanov.profiles), 1)
		row = ivanov.profiles[0]
		self.assertEqual(row.profile, f"{S1}:{PRF(1)}")  # 11. clickable link to the profile
		self.assertEqual((row.orgs_mode, row.orgs_text), ("only", "Ромашка ООО"))
		self.assertEqual(
			row.restrictions_text,
			"Организации: только: Ромашка ООО\nФизическиеЛица: только: Иванов Иван Иванович",
		)
		self.assertEqual(
			[r["kind"] for r in json.loads(row.restrictions_json)], ["Организации", "ФизическиеЛица"]
		)
		self.assertTrue(frappe.db.exists("IB Access Profile", row.profile))

		# The 1C user card without an IB user has no roles and no profiles
		card = frappe.get_doc("IB User", f"{S1}:{USR(5)}")
		self.assertEqual((card.login_allowed, card.invalid, len(card.profiles)), (0, 1, 0))
		self.assertFalse(card.has_extra_roles)

	def test_orgs_text_modes(self):
		self.imp()
		petrova = frappe.get_doc("IB User", f"{S1}:{USR(2)}")
		rows = {r.access_group_name: r for r in petrova.profiles}
		self.assertEqual(rows["Бухгалтер"].orgs_text, "все, кроме: Ромашка Сервис ООО")
		self.assertEqual(rows["Отдел кадров"].orgs_text, "не настроено")
		self.assertEqual(rows["Отдел кадров"].direct, 0)
		self.assertEqual((petrova.all_orgs, petrova.orgs_text), (1, "все"))
		admin = frappe.get_doc("IB User", f"{S1}:{USR(3)}")
		self.assertEqual(
			(admin.profiles[0].orgs_mode, admin.profiles[0].orgs_text),
			("unrestricted", "все (без ограничения)"),
		)
		fedorov = frappe.get_doc("IB User", f"{S1}:{USR(6)}")
		self.assertEqual(fedorov.profiles[0].orgs_text, "ни одной")
		self.assertEqual(
			importer.orgs_text([{"kind": "Организации", "mode": "all", "values": []}]), ("все", "all")
		)

	# ---------------------------------------------------------------- 3. idempotency

	def test_repeat_import_changes_nothing(self):
		self.imp()
		before = len(self.versions()) + len(self.versions("IB Access Profile"))
		result = self.imp()
		self.assertEqual(result["changed"], 0)
		self.assertEqual(result["missing"], 0)
		self.assertEqual(len(self.versions()) + len(self.versions("IB Access Profile")), before)

		# 1C may return lists in any order: still nothing changes
		shuffled = snapshot()
		for item in shuffled["users"]:
			if item.get("ib"):
				item["ib"]["roles"].reverse()
		for item in shuffled["profiles"]:
			item["roles"].reverse()
		shuffled["user_rights"].reverse()
		user_rights(shuffled, 2)["profiles"].reverse()
		self.assertEqual(self.imp(shuffled)["changed"], 0)

	# ---------------------------------------------------------------- 4. profile removed

	def test_profile_removed_from_user_is_visible_in_version(self):
		self.imp()
		name = f"{S1}:{USR(2)}"
		row_names = {r.access_group_name: r.name for r in frappe.get_doc("IB User", name).profiles}
		data = snapshot()
		rights = user_rights(data, 2)
		rights["profiles"] = [p for p in rights["profiles"] if p["access_group_name"] != "Отдел кадров"]
		result = self.imp(data)
		self.assertEqual(result["changed"], 1)

		doc = frappe.get_doc("IB User", name)
		self.assertEqual([r.access_group_name for r in doc.profiles], ["Бухгалтер"])
		self.assertEqual(
			doc.profiles[0].name, row_names["Бухгалтер"]
		)  # the remaining row is kept, not recreated
		diff = json.loads(self.versions(name=name)[-1].data)
		self.assertEqual(
			[r[1]["access_group_name"] for r in diff["removed"] if r[0] == "profiles"], ["Отдел кадров"]
		)
		self.assertFalse([r for r in diff["added"] if r[0] == "profiles"])
		# ЧтениеКадровыхДанных was in the removed profile only: now a role bypassing profiles
		self.assertEqual(doc.extra_roles, "ЧтениеКадровыхДанных")

	# ---------------------------------------------------------------- 5. missing and back

	def test_user_missing_and_back(self):
		self.imp()
		name = f"{S1}:{USR(4)}"
		data = snapshot()
		data["users"] = [u for u in data["users"] if u["id"] != USR(4)]
		self.assertEqual(self.imp(data)["missing"], 1)
		self.assertEqual(frappe.db.get_value("IB User", name, "missing_in_source"), 1)
		self.assertIn("missing_in_source", self.versions(name=name)[-1].data)

		result = self.imp()
		self.assertEqual(result["missing"], 0)
		self.assertEqual(frappe.db.get_value("IB User", name, "missing_in_source"), 0)

		data["profiles"] = [p for p in data["profiles"] if p["id"] != PRF(4)]
		self.imp(data)
		self.assertEqual(frappe.db.get_value("IB Access Profile", f"{S1}:{PRF(4)}", "missing_in_source"), 1)

	# ---------------------------------------------------------------- 6. extra roles

	def test_extra_roles(self):
		self.imp()
		ivanov = frappe.get_doc("IB User", f"{S1}:{USR(1)}")
		self.assertEqual(ivanov.extra_roles, "ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок")
		self.assertEqual(ivanov.has_extra_roles, 1)
		in_profiles = {r.role_name: r.in_profiles for r in ivanov.ib_roles}
		self.assertEqual(in_profiles["ЧтениеКадровыхДанных"], 1)
		self.assertEqual(in_profiles["ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок"], 0)
		# The role of a profile received through a user group is not extra
		self.assertEqual(frappe.db.get_value("IB User", f"{S1}:{USR(2)}", "has_extra_roles"), 0)
		# No access groups at all: every role is extra
		self.assertEqual(
			frappe.db.get_value("IB User", f"{S1}:{USR(4)}", "extra_roles"), "ЧтениеКадровыхДанных"
		)

	def test_profile_role_change_refreshes_users(self):
		self.imp()
		data = snapshot()
		profile = next(p for p in data["profiles"] if p["id"] == PRF(1))
		profile["roles"].append(
			{"name": "ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок", "title": "Открытие внешних отчётов"}
		)
		self.imp(data)
		ivanov = frappe.get_doc("IB User", f"{S1}:{USR(1)}")
		self.assertEqual(ivanov.has_extra_roles, 0)
		self.assertEqual(ivanov.extra_roles, "")

	# ---------------------------------------------------------------- 7. orphans

	def test_orphans(self):
		self.imp()
		orphan = frappe.get_doc("IB User", f"{S1}:ib:{IB(9)}")
		self.assertEqual(orphan.is_orphan, 1)
		self.assertEqual(orphan.user_name, "robot_exchange")  # no full name → login
		self.assertEqual(orphan.extra_roles, "ПолныеПрава")
		self.assertEqual(len(orphan.profiles), 0)

	# ---------------------------------------------------------------- folders

	def test_is_folder(self):
		self.imp()
		self.assertEqual(frappe.db.get_value("IB Access Profile", f"{S1}:{PRF(4)}", "is_folder"), 1)
		# No roles, but a user refers to it: not a folder
		self.assertEqual(frappe.db.get_value("IB Access Profile", f"{S1}:{PRF(5)}", "is_folder"), 0)
		self.assertEqual(frappe.db.get_value("IB Access Profile", f"{S1}:{PRF(1)}", "is_folder"), 0)

	# ---------------------------------------------------------------- 8. bases are isolated

	def test_two_bases_do_not_interfere(self):
		self.imp(base=S1)
		other = snapshot()
		other["users"] = other["users"][:1]
		other["ib_orphans"] = []
		self.imp(other, base=S2)
		self.assertEqual(frappe.db.count("IB User", {"base_code": S1, "missing_in_source": 1}), 0)
		self.assertEqual(frappe.db.count("IB User", {"base_code": S2}), 1)
		self.assertTrue(frappe.db.exists("IB User", f"{S2}:{USR(1)}"))
		# Emptying base 2 does not touch base 1
		self.imp({"users": [], "profiles": [], "user_rights": [], "ib_orphans": []}, base=S2)
		self.assertEqual(frappe.db.count("IB User", {"base_code": S1, "missing_in_source": 0}), 7)

	def test_base_code_resolution(self):
		self.assertEqual(importer.resolve_base_code("tst1"), S1)
		self.assertRaises(frappe.DoesNotExistError, importer.resolve_base_code, "zup9")

	# ---------------------------------------------------------------- person link

	def hr_sync(self):
		log = run_source_sync(S1, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Успех", log.messages)

	def test_person_link_via_hr_mirror(self):
		self.hr_sync()
		self.imp()
		person = frappe.db.get_value("Person Source ID", {"source": S1, "person_guid": FL(1)}, "parent")
		ivanov = frappe.db.get_value(
			"IB User", f"{S1}:{USR(1)}", ["person", "person_link_method"], as_dict=True
		)
		self.assertEqual((ivanov.person, ivanov.person_link_method), (person, "GUID физлица (ЗУП)"))
		admin = frappe.db.get_value("IB User", f"{S1}:{USR(3)}", ["person", "person_link_note"], as_dict=True)
		self.assertFalse(admin.person)
		self.assertIn("не найден", admin.person_link_note)
		self.assertEqual(frappe.db.get_value("IB User", f"{S1}:{USR(1)}", "base_configuration"), "ЗУП")
		# Another ZUP base: its GUIDs are unknown to the HR data of TST1, the full name still matches
		self.imp(base=S2)
		other = frappe.db.get_value(
			"IB User", f"{S2}:{USR(1)}", ["person", "person_link_method", "person_link_note"], as_dict=True
		)
		self.assertEqual((other.person, other.person_link_method), (person, "ФИО"))

		# Report: login allowed although the employee does not work (Фёдоров is dismissed in zup1)
		from access_registry.access_catalog.report.ib_login_not_working.ib_login_not_working import execute

		_columns, rows = execute({"base_code": S1})
		self.assertEqual([r.name for r in rows], [f"{S1}:{USR(6)}"])
		self.assertEqual(rows[0].person_status, "Уволен")

	def test_accounting_base(self):
		"""A non-ZUP base: users and rights only, employees are found by full name."""
		self.hr_sync()
		data = snapshot()
		for user in data["users"]:
			if user.get("person_id"):
				user["person_id"] = user["person_id"].replace(
					"e000", "e999"
				)  # own GUIDs of the accounting base
		result = self.imp(data, base=BP)
		self.assertEqual(result["users"], 6)
		ivanov = frappe.get_doc("IB User", f"{BP}:{USR(1)}")
		self.assertEqual(ivanov.base_configuration, "Бухгалтерия")
		self.assertEqual(ivanov.person_link_method, "ФИО")
		self.assertEqual(
			ivanov.person,
			frappe.db.get_value("Person Source ID", {"source": S1, "person_guid": FL(1)}, "parent"),
		)
		# Person card shows the user in «Учётные записи»
		self.assertIn("IB User", [link.link_doctype for link in frappe.get_meta("Person").links])

		# Kadry cannot be synced from an accounting base
		log = run_source_sync(BP, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("только из баз ЗУП", log.messages)
		with patch("access_registry.sync.engine.enqueue_source_sync") as enqueue:
			from access_registry.sync.engine import enqueue_all_sources

			enqueue_all_sources()
		self.assertNotIn(BP, [c.args[0] for c in enqueue.call_args_list])
		self.assertIn(S1, [c.args[0] for c in enqueue.call_args_list])

	def test_namesakes_and_manual_link(self):
		self.hr_sync()
		# A second «Иванов Иван Иванович» who does not work: the working one is chosen
		namesake = frappe.get_doc(
			{
				"doctype": "Person",
				"last_name": "Иванов",
				"first_name": "Иван",
				"middle_name": "Иванович",
				"status": "Уволен",
			}
		).insert()
		self.imp(base=BP)
		working = frappe.db.get_value("Person Source ID", {"source": S1, "person_guid": FL(1)}, "parent")
		self.assertEqual(frappe.db.get_value("IB User", f"{BP}:{USR(1)}", "person"), working)
		# Two working namesakes: ambiguous, nobody is linked, the reason is shown
		frappe.db.set_value("Person", namesake.name, "status", "Работает")
		data = snapshot()
		data["users"][0]["department_name"] = "Бухгалтерия"  # change the user so that it is re-imported
		self.imp(data, base=BP)
		user = frappe.get_doc("IB User", f"{BP}:{USR(1)}")
		self.assertFalse(user.person)
		self.assertIn("несколько сотрудников", user.person_link_note)

		# A manual link wins and survives the next import
		user.manual_person = namesake.name
		user.save()
		self.assertEqual((user.person, user.person_link_method), (namesake.name, "Вручную"))
		data["users"][0]["department_name"] = "Бухгалтерия и налоги"
		self.imp(data, base=BP)
		user.reload()
		self.assertEqual((user.person, user.person_link_method), (namesake.name, "Вручную"))

		from access_registry.access_catalog.report.ib_users_without_employee import (
			ib_users_without_employee as without,
		)

		rows = without.execute({"base_code": BP})[1]
		self.assertNotIn(f"{BP}:{USR(1)}", [r.name for r in rows])
		self.assertIn(f"{BP}:{USR(3)}", [r.name for r in rows])  # «Администратор» is nobody

	# ---------------------------------------------------------------- guard

	def test_guard(self):
		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 4)
		self.imp()
		data = snapshot()
		data["users"], data["ib_orphans"] = data["users"][:2], []
		self.assertRaises(importer.CatalogGuardTripped, self.imp, data)

	# ---------------------------------------------------------------- 9. event log

	def test_import_log_and_cursor(self):
		self.imp()
		result = importer.import_log_data(S1, log_payload())
		self.assertEqual(result, {"inserted": 3, "skipped": 0})
		self.assertEqual(importer.import_log_data(S1, log_payload()), {"inserted": 0, "skipped": 3})

		events = frappe.get_all("IB Audit Event", fields=["*"], order_by="event_date")
		self.assertEqual(events[0].event_title, "Данные изменены")
		self.assertEqual(events[1].event_title, "Пользователь ИБ изменён")
		self.assertEqual(events[2].who_user, f"{S1}:{USR(1)}")
		# 14:02:11+03:00 in the site time zone
		expected = importer.to_site_datetime("2026-09-27T14:02:11+03:00")
		self.assertEqual(events[0].event_date, expected)

		cursor = importer.to_site_datetime(importer.log_cursor(S1)["from"])
		self.assertEqual(str(cursor), str(importer.to_site_datetime("2026-09-27T16:29:00+03:00")))
		self.assertEqual(
			importer.import_log_data(S2, log_payload())["inserted"], 3
		)  # another base, other keys

	def test_log_cursor_default(self):
		cursor = importer.to_site_datetime(importer.log_cursor(S1)["from"])
		age = frappe.utils.now_datetime() - cursor
		self.assertTrue(6.9 < age.total_seconds() / 86400 < 7.1)

	# ---------------------------------------------------------------- 10. API permissions

	def test_api_requires_role(self):
		user = "catalog-viewer@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "Viewer", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("Access Catalog Viewer")
		frappe.set_user(user)
		# frappe.only_for() is a no-op under the test runner: switch the flag off around the calls
		frappe.flags.in_test = False
		try:
			self.assertRaises(frappe.PermissionError, api.import_snapshot, S1, snapshot())
			self.assertRaises(frappe.PermissionError, api.import_log, S1, log_payload())
			self.assertRaises(frappe.PermissionError, api.get_log_cursor, S1)
		finally:
			frappe.flags.in_test = True
		self.assertEqual(frappe.db.count("IB User"), 0)

	def test_api_with_sync_role(self):
		user = "n8n@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "n8n", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("1C Sync")
		frappe.set_user(user)
		result = api.import_snapshot(S1, json.dumps(snapshot(), ensure_ascii=False))
		self.assertEqual(result["users"], 6)
		self.assertEqual(api.import_log(S1, json.dumps(log_payload()))["inserted"], 3)
		self.assertIn("from", api.get_log_cursor(S1))

	# ---------------------------------------------------------------- pull by Frappe

	def test_pull_snapshot_and_log(self):
		frappe.db.set_value(
			"Info Base", S1, {"itaccess_enabled": 1, "itaccess_url": "http://127.0.0.1:9/hs/itaccess"}
		)
		log = run_snapshot(S1, commit=False, fetch_snapshot=lambda *a, **k: snapshot())
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertEqual(log.kind, "Права 1С")
		self.assertEqual(json.loads(log.stats)["users"], 6)
		self.assertTrue(frappe.db.get_value("Info Base", S1, "itaccess_last_snapshot"))
		self.assertEqual(
			frappe.db.get_value("IB User", f"{S1}:{USR(1)}", "modified_by"), "sync-zup@access.local"
		)
		self.assertEqual(frappe.session.user, "Administrator")

		calls = []

		def fake_log(source, path, params=None, timeout=None):
			calls.append(params)
			return log_payload()

		self.assertEqual(run_log(S1, commit=False, fetch_log=fake_log)["inserted"], 3)
		self.assertEqual(run_log(S1, commit=False, fetch_log=fake_log)["inserted"], 0)
		self.assertIn("from", calls[0])
		# A quiet run does not write a Sync Log
		self.assertEqual(frappe.db.count("Sync Log", {"kind": "Журнал 1С"}), 1)

	def test_pull_error_is_logged_and_rolled_back(self):
		self.imp()

		def broken(*args, **kwargs):
			data = snapshot()
			data["users"][0]["name"] = "Новое имя"
			data["users"][1]["ib"]["roles"] = 5  # invalid payload → error after the first user was saved
			return data

		log = run_snapshot(S1, commit=False, fetch_snapshot=broken)
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("Traceback", log.messages)
		self.assertEqual(
			frappe.db.get_value("IB User", f"{S1}:{USR(1)}", "user_name"), "Иванов Иван Иванович"
		)

	# ---------------------------------------------------------------- reports

	def test_reports_run(self):
		self.imp()
		importer.import_log_data(S1, log_payload())
		from access_registry.access_catalog.report.ib_all_organizations_access import (
			ib_all_organizations_access as all_orgs,
		)
		from access_registry.access_catalog.report.ib_extra_roles import ib_extra_roles as extra
		from access_registry.access_catalog.report.ib_orphans import ib_orphans as orphans
		from access_registry.access_catalog.report.ib_profile_users import ib_profile_users as profile_users
		from access_registry.access_catalog.report.ib_rights_changes import ib_rights_changes as changes
		from access_registry.access_catalog.report.ib_users_without_employee import (
			ib_users_without_employee as no_person,
		)

		# Orphans have no profiles, so all their roles bypass profiles
		self.assertEqual(
			sorted(r.name for r in extra.execute({})[1]),
			sorted([f"{S1}:{USR(1)}", f"{S1}:{USR(4)}", f"{S1}:ib:{IB(9)}"]),
		)
		self.assertEqual([r.name for r in orphans.execute({})[1]], [f"{S1}:ib:{IB(9)}"])
		# No HR data loaded: every user allowed to log in is without an employee
		self.assertEqual(
			sorted(r.name for r in no_person.execute({"configuration": "ЗУП"})[1]),
			sorted([f"{S1}:{USR(n)}" for n in (1, 2, 3, 6)] + [f"{S1}:ib:{IB(9)}"]),
		)
		self.assertEqual(
			sorted(r.name for r in all_orgs.execute({})[1]), sorted([f"{S1}:{USR(2)}", f"{S1}:{USR(3)}"])
		)
		users = profile_users.execute({"profile": f"{S1}:{PRF(1)}"})[1]
		self.assertEqual(sorted(r.name for r in users), sorted([f"{S1}:{USR(1)}", f"{S1}:{USR(2)}"]))
		rows = changes.execute({"from_date": "2026-09-27", "to_date": "2026-09-27", "who": "Админ"})[1]
		self.assertEqual(len(rows), 2)

	def test_workspace_has_rights_block(self):
		ws = frappe.get_doc("Workspace", "Access Registry")
		targets = {s.link_to for s in ws.shortcuts} | {
			link.link_to for link in ws.links if link.type == "Link"
		}
		for name in (
			"IB User",
			"IB Access Profile",
			"IB Audit Event",
			"IB Rights Changes",
			"IB Login Not Working",
		):
			self.assertIn(name, targets)
		for link in ws.links:
			if link.type == "Link" and link.link_type == "Report":
				self.assertTrue(frappe.db.exists("Report", link.link_to), link.link_to)

	def test_full_access_report(self):
		from access_registry.access_catalog.access_report import execute

		self.hr_sync()
		self.imp()
		self.imp(base=BP)
		_columns, rows = execute({})
		by_key = {(r["user"]): r for r in rows}
		ivanov = by_key[f"{S1}:{USR(1)}"]
		self.assertEqual(ivanov["employee"], "Иванов Иван Иванович")
		self.assertEqual(ivanov["position"], "Генеральный директор")
		self.assertEqual(ivanov["department_title"], "Дирекция")
		self.assertEqual(ivanov["organization_title"], "Ромашка ООО")
		self.assertEqual(ivanov["employment_kind"], "Основное место работы")
		self.assertEqual(ivanov["profiles"], "Кадровик")
		self.assertEqual(ivanov["base_configuration"], "ЗУП")
		# The same employee in the accounting base, found by full name
		self.assertEqual(by_key[f"{BP}:{USR(1)}"]["organization_title"], "Ромашка ООО")
		self.assertEqual(by_key[f"{BP}:{USR(1)}"]["base_configuration"], "Бухгалтерия")
		# Users without the right to log in are hidden by default
		self.assertNotIn(f"{S1}:{USR(4)}", by_key)
		self.assertIn(f"{S1}:{USR(4)}", {r["user"] for r in execute({"include_disabled": 1})[1]})

		# One row per profile with its restrictions
		petrova = [r for r in execute({"by_profile": 1, "base_code": S1})[1] if r["user"] == f"{S1}:{USR(2)}"]
		self.assertEqual(sorted(r["access_group"] for r in petrova), ["Бухгалтер", "Отдел кадров"])
		self.assertIn(
			"все, кроме", next(r for r in petrova if r["access_group"] == "Бухгалтер")["restrictions"]
		)

		# Filters by HR context and unlinked users
		org = frappe.db.get_value("HR Organization", {"source": S1, "title": "Ромашка ООО"}, "name")
		self.assertTrue(all(r["organization"] == org for r in execute({"organization": org})[1]))
		unlinked = execute({"only_unlinked": 1})[1]
		self.assertTrue(unlinked and all(not r["person"] for r in unlinked))
