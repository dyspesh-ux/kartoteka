"""Tests of the Active Directory mirror: accounts, groups, membership and links to employees."""

import copy
import datetime
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.access_catalog import importer
from access_registry.active_directory import ldap_client
from access_registry.active_directory.sync import ou_of, run_domain_sync
from access_registry.sync.departments import ensure_root
from access_registry.sync.engine import run_source_sync
from access_registry.tests.test_catalog import BP, CATALOG_DOCTYPES, FL, HR_DOCTYPES, S1, USR, snapshot
from access_registry.tests.test_zup_sync import TODAY, load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "ad")
DOMAIN = "TSTAD"
AD_DOCTYPES = ["AD Account Group", "AD Account", "AD Group", "AD Domain"]


def ACC(n):
	return f"00000000-0000-4000-ad00-{n:012d}"


def GRP(n):
	return f"00000000-0000-4000-ad10-{n:012d}"


def directory() -> dict:
	with open(os.path.join(FIXTURES, "directory.json"), encoding="utf-8") as fh:
		return json.load(fh)


def user(data, n):
	return next(u for u in data["users"] if u["objectGUID"] == ACC(n))


class TestActiveDirectory(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in AD_DOCTYPES + CATALOG_DOCTYPES + HR_DOCTYPES:
			frappe.db.delete(doctype)
		frappe.db.delete("Version", {"ref_doctype": ["like", "AD %"]})
		ensure_root()
		frappe.get_doc(
			{"doctype": "Info Base", "source_code": S1, "title": S1, "base_url": "http://127.0.0.1:9/hs"}
		).insert()
		frappe.get_doc(
			{
				"doctype": "Info Base",
				"source_code": BP,
				"title": "Бухгалтерия",
				"configuration": "Бухгалтерия",
			}
		).insert()
		frappe.get_doc(
			{
				"doctype": "AD Domain",
				"domain_code": DOMAIN,
				"title": "corp.example.local",
				"netbios_name": "corp",
				"dns_name": "corp.example.local",
				"ldap_url": "ldaps://dc1.corp.example.local",
				"base_dn": "DC=corp,DC=example,DC=local",
				"bind_user": "CORP\\svc_registry",
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def hr_sync(self):
		log = run_source_sync(S1, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Успех", log.messages)

	def sync(self, data=None):
		payload = copy.deepcopy(data or directory())
		return run_domain_sync(DOMAIN, commit=False, fetch=lambda domain: payload)

	def assertSuccess(self, log):
		self.assertEqual(log.status, "Успех", log.messages)
		return json.loads(log.stats)

	def account(self, n):
		return frappe.get_doc("AD Account", f"{DOMAIN}:{ACC(n)}")

	def person(self, n):
		return frappe.db.get_value("Person Source ID", {"source": S1, "person_guid": FL(n)}, "parent")

	def versions(self, doctype="AD Account"):
		return frappe.get_all("Version", filters={"ref_doctype": doctype}, fields=["docname", "data"])

	# ---------------------------------------------------------------- import

	def test_import_accounts_and_groups(self):
		self.hr_sync()
		stats = self.assertSuccess(self.sync())
		self.assertEqual((stats["accounts"], stats["groups"]), (7, 3))
		self.assertEqual(frappe.db.get_value("AD Domain", DOMAIN, "netbios_name"), "CORP")

		ivanov = self.account(1)
		self.assertEqual((ivanov.sam_account_name, ivanov.enabled, ivanov.locked), ("Ivanov", 1, 0))
		self.assertEqual(ivanov.ou, "Компания / Сотрудники")
		self.assertEqual((ivanov.person, ivanov.person_link_method), (self.person(1), "ФИО"))
		self.assertEqual(sorted(r.group_name for r in ivanov.groups), ["GG_1C_ZUP_Users", "Рассылка всем"])
		self.assertEqual(ivanov.group_count, 2)
		self.assertEqual(str(ivanov.last_logon), "2026-05-30 08:00:00")

		self.assertEqual(self.account(2).manager, ivanov.name)
		self.assertEqual(self.account(3).enabled, 0)
		svc = self.account(6)
		self.assertEqual((svc.password_never_expires, svc.person), (1, None))
		self.assertEqual(svc.ou, "Компания / Служебные")
		orlova = self.account(7)
		self.assertFalse(orlova.person)
		self.assertIn("не найден", orlova.person_link_note)

		zup = frappe.get_doc("AD Group", f"{DOMAIN}:{GRP(1)}")
		self.assertEqual((zup.scope, zup.security, zup.member_count), ("Глобальная", 1, 2))
		self.assertEqual(zup.managed_by, ivanov.name)
		buh = frappe.get_doc("AD Group", f"{DOMAIN}:{GRP(2)}")
		self.assertEqual((buh.scope, buh.security, buh.member_count), ("Локальная в домене", 1, 2))
		mailing = frappe.get_doc("AD Group", f"{DOMAIN}:{GRP(3)}")
		self.assertEqual((mailing.scope, mailing.security), ("Глобальная", 0))
		# AD accounts are shown on the employee card
		self.assertIn("AD Account", [link.link_doctype for link in frappe.get_meta("Person").links])

	def test_repeat_sync_changes_nothing(self):
		self.hr_sync()
		self.sync()
		before = len(self.versions()) + len(self.versions("AD Group"))
		stats = self.assertSuccess(self.sync())
		self.assertEqual(stats.get("AD Account: изменено", 0) + stats.get("AD Group: изменено", 0), 0)
		self.assertEqual(stats["AD Account: без изменений"], 7)

		# Logon time changes every day: stored, but not versioned and not a change of the card
		data = directory()
		user(data, 1)["lastLogonTimestamp"] = "2026-06-01T07:30:00"
		user(data, 1)["whenChanged"] = "2026-06-01T07:30:00"
		user(data, 2)["lockoutTime"] = "2026-06-01T07:00:00"
		stats = self.assertSuccess(self.sync(data))
		self.assertEqual(stats.get("AD Account: изменено", 0), 0)
		self.assertEqual(stats["last_logon_updated"], 2)
		self.assertEqual(str(self.account(1).last_logon), "2026-06-01 07:30:00")
		self.assertEqual(self.account(2).locked, 1)
		self.assertEqual(len(self.versions()) + len(self.versions("AD Group")), before)

	def test_changes_are_versioned(self):
		self.hr_sync()
		self.sync()
		data = directory()
		user(data, 4)["userAccountControl"] = 514
		user(data, 1)["memberOf"].append("CN=DL_Buh_Read,OU=Группы,DC=corp,DC=example,DC=local")
		stats = self.assertSuccess(self.sync(data))
		self.assertEqual(stats["AD Account: изменено"], 2)
		self.assertEqual(self.account(4).enabled, 0)
		self.assertEqual(frappe.db.get_value("AD Group", f"{DOMAIN}:{GRP(2)}", "member_count"), 3)
		changes = {v.docname: v.data for v in self.versions()}
		self.assertIn('"enabled"', changes[f"{DOMAIN}:{ACC(4)}"])
		self.assertIn("DL_Buh_Read", changes[f"{DOMAIN}:{ACC(1)}"])

	def test_missing_and_guard(self):
		self.sync()
		data = directory()
		data["users"] = [u for u in data["users"] if u["objectGUID"] != ACC(5)]
		stats = self.assertSuccess(self.sync(data))
		self.assertEqual(stats["accounts_missing"], 1)
		self.assertEqual(self.account(5).missing_in_source, 1)
		self.assertSuccess(self.sync())
		self.assertEqual(self.account(5).missing_in_source, 0)

		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 5)
		data = directory()
		data["users"] = data["users"][:2]
		log = self.sync(data)
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertIn("предохранителем", log.messages)
		self.assertEqual(frappe.db.count("AD Account", {"domain": DOMAIN, "missing_in_source": 0}), 7)

	def test_error_is_logged(self):
		def broken(domain):
			raise ConnectionError("dc1 unreachable")

		log = run_domain_sync(DOMAIN, commit=False, fetch=broken)
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("dc1 unreachable", log.messages)
		self.assertIn("Ошибка", frappe.db.get_value("AD Domain", DOMAIN, "last_status"))

	# ---------------------------------------------------------------- employee links

	def test_employee_number_wins_and_manual_link(self):
		self.hr_sync()
		data = directory()
		# Орлова cannot be found by «Орлова Е. П.», but employeeNumber holds her UUID
		user(data, 7)["employeeNumber"] = self.person(6).upper()
		user(data, 3)["employeeNumber"] = "00000000-dead-beef-0000-000000000000"
		self.sync(data)
		orlova = self.account(7)
		self.assertEqual((orlova.person, orlova.person_link_method), (self.person(6), "employeeNumber"))
		sidorov = self.account(3)
		self.assertEqual((sidorov.person, sidorov.person_link_method), (self.person(3), "ФИО"))

		svc = self.account(6)
		svc.manual_person = self.person(1)
		svc.save()
		self.assertEqual((svc.person, svc.person_link_method), (self.person(1), "Вручную"))
		data = directory()
		user(data, 6)["mail"] = "backup@example.com"
		self.sync(data)
		svc.reload()
		self.assertEqual((svc.person, svc.person_link_method), (self.person(1), "Вручную"))

	def test_1c_users_linked_through_ad(self):
		self.hr_sync()
		# Accounting base: the full name is spoiled, only the AD login tells who it is
		data = snapshot()
		for item in data["users"]:
			if item.get("person_id"):
				item["person_id"] = item["person_id"].replace("e000", "e999")
		data["users"][0]["name"] = data["users"][0]["person_name"] = "Иванов И.И. (бух)"
		importer.import_snapshot_data(BP, copy.deepcopy(data))
		self.assertFalse(frappe.db.get_value("IB User", f"{BP}:{USR(1)}", "person"))

		stats = self.assertSuccess(self.sync())
		self.assertEqual(stats["ib_users_relinked"], 1)
		ib_user = frappe.get_doc("IB User", f"{BP}:{USR(1)}")
		self.assertEqual(ib_user.ad_account, f"{DOMAIN}:{ACC(1)}")
		self.assertEqual((ib_user.person, ib_user.person_link_method), (self.person(1), "Учётка AD"))

		# The next snapshot resolves through AD straight away: no real change, no new version
		versions = frappe.db.count("Version", {"ref_doctype": "IB User"})
		importer.import_snapshot_data(BP, copy.deepcopy(data))
		self.assertEqual(frappe.db.count("Version", {"ref_doctype": "IB User"}), versions)
		ib_user.reload()
		self.assertEqual(ib_user.person_link_method, "Учётка AD")
		result, _warnings = importer.import_snapshot_data(BP, copy.deepcopy(data))
		self.assertEqual(result["changed"], 0)
		# ZUP GUID still beats AD in the ZUP base
		importer.import_snapshot_data(S1, snapshot())
		zup_user = frappe.get_doc("IB User", f"{S1}:{USR(1)}")
		self.assertEqual(zup_user.person_link_method, "GUID физлица (ЗУП)")
		self.assertEqual(zup_user.ad_account, f"{DOMAIN}:{ACC(1)}")

		# AD account disabled while 1C login is still allowed
		from access_registry.active_directory.report.ad_disabled_but_1c_active.ad_disabled_but_1c_active import (
			execute,
		)

		self.assertEqual(execute({})[1], [])
		directory_data = directory()
		user(directory_data, 1)["userAccountControl"] = 514
		self.sync(directory_data)
		rows = execute({})[1]
		self.assertEqual(sorted(r.name for r in rows), sorted([f"{BP}:{USR(1)}", f"{S1}:{USR(1)}"]))

	# ---------------------------------------------------------------- reports

	def run_report(self, name, filters=None):
		module = frappe.get_module(f"access_registry.active_directory.report.{name}.{name}")
		return module.execute(filters or {})

	def names(self, rows):
		return sorted(r.name.split(":")[1] for r in rows)

	def test_reports(self):
		self.hr_sync()
		self.sync()
		# Фёдоров and Новиков are dismissed in zup1, their accounts are enabled
		self.assertEqual(self.names(self.run_report("ad_dismissed_enabled")[1]), [ACC(4), ACC(5)])
		self.assertEqual(self.names(self.run_report("ad_without_employee")[1]), [ACC(6), ACC(7)])
		self.assertEqual(self.names(self.run_report("ad_password_never_expires")[1]), [ACC(6)])
		inactive = self.run_report("ad_inactive_accounts", {"days": 30})[1]
		self.assertIn(ACC(5), self.names(inactive))  # never logged on
		self.assertNotIn(ACC(3), self.names(inactive))  # disabled accounts are not listed
		members = self.run_report("ad_group_members", {"group": f"{DOMAIN}:{GRP(3)}"})[1]
		self.assertEqual(self.names(members), [ACC(1), ACC(3)])
		self.assertEqual(self.run_report("ad_group_members"), ([], []))
		numbers = self.run_report("ad_employeenumber")[1]
		self.assertIn(ACC(1), self.names(numbers))
		self.assertNotIn(ACC(6), self.names(numbers))
		for report in frappe.get_all("Report", filters={"module": "Active Directory"}, pluck="name"):
			frappe.get_doc("Report", report)  # every report is installed

	# ---------------------------------------------------------------- LDAP client

	def test_value_conversion(self):
		guid = "12345678-1234-5678-1234-567812345678"
		import uuid

		self.assertEqual(ldap_client.guid_to_str(uuid.UUID(guid).bytes_le), guid)
		self.assertEqual(ldap_client.guid_to_str("{" + guid.upper() + "}"), guid)
		self.assertIsNone(ldap_client.filetime_to_datetime(0))
		self.assertIsNone(ldap_client.filetime_to_datetime(0x7FFFFFFFFFFFFFFF))
		self.assertEqual(
			ldap_client.filetime_to_datetime(133000000000000000),
			datetime.datetime(2022, 6, 18, 4, 26, 40),
		)
		self.assertEqual(
			ldap_client.generalized_time("20240110090000.0Z"), datetime.datetime(2024, 1, 10, 9, 0)
		)
		self.assertEqual(
			ou_of("CN=Иванов\\, Иван,OU=Москва,OU=Сотрудники,DC=corp,DC=local"), "Сотрудники / Москва"
		)
		row = ldap_client.normalize(
			{"memberOf": ["b", "a"], "userAccountControl": ["514"], "sAMAccountName": [b"x"]},
			["memberOf", "userAccountControl", "sAMAccountName", "mail"],
		)
		self.assertEqual(
			row, {"memberOf": ["a", "b"], "userAccountControl": 514, "sAMAccountName": "x", "mail": None}
		)

	def test_fetch_directory_with_mock_ldap(self):
		import ldap3

		server = ldap3.Server("dc1", get_info=ldap3.OFFLINE_AD_2012_R2)
		conn = ldap3.Connection(
			server, user="CN=svc,DC=corp,DC=example,DC=local", password="x", client_strategy=ldap3.MOCK_SYNC
		)
		conn.strategy.add_entry(
			"CN=svc,DC=corp,DC=example,DC=local", {"userPassword": "x", "sAMAccountName": "svc"}
		)
		guid = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
		conn.strategy.add_entry(
			"CN=Иванов Иван Иванович,OU=Сотрудники,DC=corp,DC=example,DC=local",
			{
				"objectClass": ["top", "person", "user"],
				"objectCategory": "person",
				"objectGUID": frappe.utils.cstr(guid),
				"sAMAccountName": "Ivanov",
				"displayName": "Иванов Иван Иванович",
				"userAccountControl": "512",
				"lastLogonTimestamp": "133000000000000000",
				"memberOf": ["CN=GG_1C_ZUP_Users,OU=Группы,DC=corp,DC=example,DC=local"],
			},
		)
		conn.strategy.add_entry(
			"CN=GG_1C_ZUP_Users,OU=Группы,DC=corp,DC=example,DC=local",
			{"objectClass": ["top", "group"], "cn": "GG_1C_ZUP_Users", "groupType": "-2147483646"},
		)
		conn.bind()
		domain = frappe.get_doc("AD Domain", DOMAIN)
		data = ldap_client.fetch_directory(domain, connection=conn)
		self.assertEqual([u["sAMAccountName"] for u in data["users"]], ["Ivanov"])
		ivanov = data["users"][0]
		self.assertEqual(ivanov["userAccountControl"], 512)
		self.assertEqual(ivanov["lastLogonTimestamp"], "2022-06-18T04:26:40")
		self.assertEqual(ivanov["memberOf"], ["CN=GG_1C_ZUP_Users,OU=Группы,DC=corp,DC=example,DC=local"])
		self.assertEqual([g["cn"] for g in data["groups"]], ["GG_1C_ZUP_Users"])
		self.assertEqual(data["groups"][0]["groupType"], -2147483646)

	# ---------------------------------------------------------------- overview page and workspace

	def test_overview_page_shows_ad(self):
		from access_registry.access_registry.page.access_overview import access_overview as page

		self.hr_sync()
		importer.import_snapshot_data(S1, snapshot())
		self.sync()
		overview = page.get_overview()
		kpis = overview["kpis"]
		self.assertEqual((kpis["ad_domains"], kpis["ad_enabled"]), (1, 6))
		self.assertEqual((kpis["ad_not_working"], kpis["ad_unlinked"], kpis["ad_off_1c_on"]), (2, 2, 0))
		self.assertEqual(overview["domains"][0].enabled_accounts, 6)
		self.assertEqual(overview["domains"][0].groups, 3)
		self.assertEqual(
			sorted(r.sam_account_name for r in overview["attention"]["ad_not_working"]),
			["fedorov", "novikov"],
		)

		card = page.get_person(self.person(1))
		self.assertEqual([a.sam_account_name for a in card["ad_accounts"]], ["Ivanov"])
		self.assertEqual(
			[g["name"] for g in card["ad_accounts"][0].groups], ["GG_1C_ZUP_Users", "Рассылка всем"]
		)
		self.assertFalse(card["ad_accounts"][0].employee_number_ok)
		self.assertEqual(card["accounts"][0].ad_state, "on")

		found = page.search("orlova")
		self.assertEqual([(r["kind"], r["id"]) for r in found], [("ad", f"{DOMAIN}:{ACC(7)}")])
		self.assertEqual(
			page.get_ad_account(f"{DOMAIN}:{ACC(7)}")["ad_accounts"][0].display_name, "Орлова Е. П."
		)

		matrix = page.get_matrix(only_working=0)
		self.assertEqual([d.name for d in matrix["domains"]], [DOMAIN])
		row = next(r for r in matrix["rows"] if r["person"] == self.person(1))
		self.assertEqual(row["ad"][DOMAIN]["groups"], 2)

	def test_workspace_has_ad_block(self):
		workspace = frappe.get_doc("Workspace", "Access Registry")
		shortcuts = {s.label: s for s in workspace.shortcuts}
		self.assertEqual(shortcuts["Учётки AD"].link_to, "AD Account")
		self.assertEqual(shortcuts["Включены у уволенных"].type, "Report")
		self.assertEqual(shortcuts["Домены AD"].link_to, "AD Domain")
		self.assertIn(
			"Active Directory", [link.label for link in workspace.links if link.type == "Card Break"]
		)
