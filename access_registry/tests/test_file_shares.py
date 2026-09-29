"""Tests of the Synology shared folder mirror: collector output, AD resolution, effective access, findings."""

import copy
import gzip
import json
import os
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.access_roles import engine
from access_registry.active_directory.groups import effective_account_groups
from access_registry.active_directory.sync import run_domain_sync
from access_registry.file_shares import api
from access_registry.file_shares import reports as share_reports
from access_registry.file_shares.access import ShareAccess
from access_registry.file_shares.sync import run_upload
from access_registry.file_shares.synology import ParseError, access_level, parse
from access_registry.sync.departments import ensure_root
from access_registry.sync.engine import run_source_sync
from access_registry.tests.test_ad import ACC, AD_DOCTYPES, GRP, directory
from access_registry.tests.test_catalog import HR_DOCTYPES, S1
from access_registry.tests.test_zup_sync import TODAY, load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "synology")
DOMAIN = "TSTAD"
SERVER = "NAS01"
SHARE_DOCTYPES = ["Folder ACL Entry", "Folder ACL", "File Share Privilege", "File Share", "File Server"]
BUH_SHARE_DN = "CN=GG_Buh_Share,OU=Группы,DC=corp,DC=example,DC=local"


def collector_output(stop_on_same=0) -> str:
	"""Output of synology/registry_collect.sh run on a synthetic tree (stub synoshare/synoacltool)."""
	with open(os.path.join(FIXTURES, f"collect_stop{stop_on_same}.txt"), encoding="utf-8") as fh:
		return fh.read()


def ad_with_nested_group() -> dict:
	"""The AD fixture plus GG_Buh_Share that contains DL_Buh_Read (petrova and fedorov are members of it)."""
	data = copy.deepcopy(directory())
	data["groups"].append(
		{
			"objectGUID": GRP(9),
			"distinguishedName": BUH_SHARE_DN,
			"cn": "GG_Buh_Share",
			"sAMAccountName": "GG_Buh_Share",
			"description": "Папка бухгалтерии на NAS",
			"groupType": -2147483646,
			"managedBy": None,
			"whenCreated": "2024-01-10T09:00:00",
			"whenChanged": "2026-09-01T10:00:00",
		}
	)
	buh_read = next(g for g in data["groups"] if g["sAMAccountName"] == "DL_Buh_Read")
	buh_read["memberOf"] = [BUH_SHARE_DN]
	return data


class TestFileShares(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in SHARE_DOCTYPES + AD_DOCTYPES + HR_DOCTYPES:
			frappe.db.delete(doctype)
		frappe.db.delete("Version", {"ref_doctype": ["in", ["Folder ACL", "File Share"]]})
		ensure_root()
		frappe.get_doc(
			{"doctype": "Info Base", "source_code": S1, "title": S1, "base_url": "http://127.0.0.1:9/hs"}
		).insert()
		log = run_source_sync(S1, today=TODAY, commit=False, fetch=lambda *a, **k: load("zup1"))
		self.assertEqual(log.status, "Успех", log.messages)
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
		ad = ad_with_nested_group()
		log = run_domain_sync(DOMAIN, commit=False, fetch=lambda _d: ad)
		self.assertEqual(log.status, "Успех", log.messages)
		frappe.get_doc(
			{"doctype": "File Server", "server_code": SERVER, "title": "NAS бухгалтерии", "domain": DOMAIN}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.db.rollback()

	def upload(self, text=None, stop_on_same=0):
		log = run_upload(SERVER, (text or collector_output(stop_on_same)).encode(), commit=False)
		return log

	def assertSuccess(self, log):
		self.assertEqual(log.status, "Успех", log.messages)
		return json.loads(log.stats)

	def account(self, n):
		return f"{DOMAIN}:{ACC(n)}"

	def folder(self, share, path):
		return frappe.get_doc("Folder ACL", {"server": SERVER, "share": f"{SERVER}:{share}", "path": path})

	def access(self, share, path) -> dict:
		rows = ShareAccess().rows({"share": f"{SERVER}:{share}"})
		return {r["login"].split("\\")[1]: r["level"] for r in rows if r["path"] == path}

	# ---------------------------------------------------------------- parser

	def test_parse_collector_output(self):
		parsed = parse(collector_output())
		self.assertTrue(parsed["complete"])
		self.assertEqual(parsed["meta"]["server"], SERVER)
		self.assertEqual(parsed["meta"]["stop_on_same"], "0")
		self.assertEqual(sorted(parsed["shares"]), ["buh", "public"])  # homes is skipped by SKIP_SHARES
		buh = parsed["shares"]["buh"]
		self.assertEqual((buh["path"], buh["comment"]), ("/volume1/buh", "Бухгалтерия"))
		self.assertIn({"name": "CORP\\GG_Buh_Share", "group": True, "level": "Изменение"}, buh["privileges"])
		self.assertIn({"name": "CORP\\sidorov", "group": False, "level": "Запрет"}, buh["privileges"])
		paths = [(f["share"], f["path"], f["depth"]) for f in parsed["folders"]]
		self.assertEqual(
			paths,
			[
				("buh", "/", 0),
				("buh", "/Архив", 1),
				("buh", "/Зарплата", 1),
				("buh", "/Отчёты/Квартальные/2026", 3),
				("buh", "/Старое", 1),
				("public", "/", 0),
			],
		)
		salary = parsed["folders"][2]
		self.assertFalse(salary["inherit_enabled"])
		self.assertEqual(salary["entries"][0]["name"], "CORP\\petrova")
		everyone = parsed["folders"][-1]["entries"][0]
		self.assertEqual((everyone["kind"], everyone["name"], everyone["allow"]), ("everyone", "", True))

	def test_collector_stops_on_same_rights(self):
		"""STOP_ON_SAME=1 does not look under /Отчёты (same rights as the root): /Отчёты/Квартальные/2026 is missed."""
		fast = [f["path"] for f in parse(collector_output(1))["folders"]]
		self.assertNotIn("/Отчёты/Квартальные/2026", fast)
		self.assertEqual(len(fast), 5)

	def test_parse_rejects_foreign_and_truncated(self):
		self.assertRaises(ParseError, parse, "hello")
		truncated = collector_output().replace("\nEND", "")
		self.assertFalse(parse(truncated)["complete"])
		log = self.upload(truncated)
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("END", log.messages)
		self.assertEqual(frappe.db.count("Folder ACL"), 0)

	def test_access_levels(self):
		self.assertEqual(access_level("rwxpdDaARWcCo"), "Полный доступ")
		self.assertEqual(access_level("rwxpdDaARWc--"), "Изменение")
		self.assertEqual(access_level("r-x---a-R-c--"), "Чтение")
		self.assertEqual(access_level("------a-R-c--"), "Особые права")
		self.assertEqual(access_level("rwxpdDaARWcCo", allow=False), "Запрет")

	# ---------------------------------------------------------------- import

	def test_import_shares_and_folders(self):
		stats = self.assertSuccess(self.upload(collector_output(1)))
		self.assertEqual((stats["shares"], stats["folders"]), (2, 5))
		self.assertEqual(stats["collector_settings"], "STOP_ON_SAME=1, MAX_DEPTH=12")
		self.assertIn("STOP_ON_SAME=1", self.upload(collector_output(1)).messages)
		server = frappe.get_doc("File Server", SERVER)
		self.assertEqual((server.shares_count, server.folders_count), (2, 5))
		self.assertTrue(server.last_status.startswith("Успех"))

		share = frappe.get_doc("File Share", f"{SERVER}:buh")
		kinds = {p.principal: (p.principal_type, p.level) for p in share.privileges}
		self.assertEqual(kinds["CORP\\GG_Buh_Share"], ("Группа AD", "Изменение"))
		self.assertEqual(kinds["CORP\\Ivanov"], ("Пользователь AD", "Чтение"))
		self.assertEqual(kinds["admin"], ("Локальный пользователь", "Изменение"))
		self.assertEqual(
			share.get("privileges", {"principal": "CORP\\GG_Buh_Share"})[0].ad_group, f"{DOMAIN}:{GRP(9)}"
		)

		archive = self.folder("buh", "/Архив")
		self.assertEqual(archive.explicit_entries, 1)
		self.assertEqual(len(archive.entries), 3)
		self.assertIn("запрет: CORP\\DL_Buh_Read", archive.issues)
		salary = self.folder("buh", "/Зарплата")
		self.assertIn("напрямую, не через группу: CORP\\petrova", salary.issues)
		self.assertIn("наследование прав от родителя отключено", salary.issues)
		old = self.folder("buh", "/Старое")
		self.assertIn("не найдены в AD (удалённые учётки или группы): CORP\\uvolen", old.issues)
		self.assertIn("локальные учётки NAS: backup", old.issues)
		self.assertNotIn("administrators", old.issues)  # standard local group of DSM is not a finding
		self.assertIn("доступ для всех", self.folder("public", "/").issues)
		self.assertFalse(self.folder("buh", "/").issues)

	def test_reload_is_idempotent_and_changes_are_versioned(self):
		self.assertSuccess(self.upload())
		stats = self.assertSuccess(self.upload())
		self.assertEqual(stats.get("Folder ACL: без изменений"), 6)
		self.assertFalse(frappe.get_all("Version", filters={"ref_doctype": "Folder ACL"}))

		changed = collector_output().replace(
			"user:CORP\\petrova:allow:rwxpdDaARWc--", "user:CORP\\petrova:allow:r-x---a-R-c--"
		)
		stats = self.assertSuccess(self.upload(changed))
		self.assertEqual(stats["Folder ACL: изменено"], 1)
		salary = self.folder("buh", "/Зарплата")
		self.assertEqual(salary.get("entries", {"principal": "CORP\\petrova"})[0].level, "Чтение")
		self.assertEqual(
			frappe.get_all("Version", filters={"ref_doctype": "Folder ACL"}, pluck="docname"), [salary.name]
		)

	def test_folder_gone_is_marked_missing(self):
		self.assertSuccess(self.upload())
		stats = self.assertSuccess(
			self.upload(collector_output(1))
		)  # the fast run does not see /Отчёты/…/2026
		self.assertEqual(stats["Folder ACL: пропало"], 1)
		self.assertEqual(self.folder("buh", "/Отчёты/Квартальные/2026").missing_in_source, 1)
		stats = self.assertSuccess(self.upload())
		self.assertEqual(self.folder("buh", "/Отчёты/Квартальные/2026").missing_in_source, 0)

	def test_guard_stops_big_shrink(self):
		self.assertSuccess(self.upload())
		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 5)
		frappe.db.set_single_value("Access Registry Settings", "shrink_threshold_pct", 10)
		frappe.clear_document_cache("Access Registry Settings", "Access Registry Settings")
		log = self.upload(collector_output(1))
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertEqual(frappe.db.count("Folder ACL", {"missing_in_source": 1}), 0)

	# ---------------------------------------------------------------- effective access

	def test_nested_groups(self):
		gg_buh = f"{DOMAIN}:{GRP(9)}"
		self.assertEqual(
			[r.group for r in frappe.get_doc("AD Group", f"{DOMAIN}:{GRP(2)}").parent_groups], [gg_buh]
		)
		groups = effective_account_groups()
		self.assertIn(gg_buh, groups[self.account(2)])  # petrova: DL_Buh_Read → GG_Buh_Share
		self.assertNotIn(gg_buh, groups[self.account(1)])
		# the role model sees access through nested groups too
		raw = engine.raw_accesses()
		petrova = frappe.db.get_value("AD Account", self.account(2), "person")
		self.assertIn(f"ad:{gg_buh}", raw[petrova])

	def test_effective_access(self):
		self.assertSuccess(self.upload())
		# root: GG_Buh_Share (through nested DL_Buh_Read) can change; Ivanov has no ACL entry at the root
		self.assertEqual(self.access("buh", "/"), {"petrova": "Изменение", "fedorov": "Изменение"})
		# personal folder: only petrova, inheritance is off
		self.assertEqual(self.access("buh", "/Зарплата"), {"petrova": "Изменение"})
		# deny for DL_Buh_Read wins over the inherited allow
		self.assertEqual(self.access("buh", "/Архив"), {})
		# Ivanov: ACL gives read, the shared folder gives read only
		self.assertEqual(
			self.access("buh", "/Отчёты/Квартальные/2026"),
			{"Ivanov": "Чтение", "petrova": "Изменение", "fedorov": "Изменение"},
		)
		# «Все» in the ACL, but DSM lets in only GG_1C_ZUP_Users (read)
		self.assertEqual(self.access("public", "/"), {"Ivanov": "Чтение", "petrova": "Чтение"})
		row = next(r for r in ShareAccess().rows({"share": f"{SERVER}:buh", "path": "/Зарплата"}))
		self.assertEqual(
			(row["via"], row["person"]),
			("CORP\\petrova", frappe.db.get_value("AD Account", self.account(2), "person")),
		)

	def test_disabled_account_has_no_access(self):
		self.assertSuccess(self.upload())
		frappe.db.set_value("AD Account", self.account(4), "enabled", 0)  # fedorov
		self.assertEqual(self.access("buh", "/"), {"petrova": "Изменение"})

	def test_reports(self):
		self.assertSuccess(self.upload())
		columns, rows = share_reports.share_access({"server": SERVER, "min_level": "Изменение"})
		self.assertTrue(columns)
		self.assertTrue(rows)
		self.assertTrue(all(r["level"] in ("Изменение", "Полный доступ") for r in rows))
		person = frappe.db.get_value("AD Account", self.account(1), "person")
		_c, mine = share_reports.share_access({"person": person})
		self.assertEqual(
			{(r["share_name"], r["path"]) for r in mine},
			{("buh", "/Отчёты/Квартальные/2026"), ("public", "/")},
		)

		_c, issues = share_reports.share_issues({"server": SERVER})
		kinds = {i["issue"] for i in issues}
		self.assertTrue({"запрет", "доступ для всех", "локальные учётки NAS"} <= kinds)
		_c, only = share_reports.share_issues({"issue": "наследование"})
		self.assertEqual([i["path"] for i in only], ["/Зарплата"])

		before = {r["login"] for r in share_reports.share_access({"only_not_working": 1})[1]}
		self.assertNotIn("CORP\\Ivanov", before)
		frappe.db.set_value("Person", person, "status", "Уволен")
		_c, issues = share_reports.share_issues({"server": SERVER})
		dismissed = [i for i in issues if i["issue"] == "доступ у неработающего" and i["person"] == person]
		self.assertEqual(
			{(i["share_name"], i["path"]) for i in dismissed},
			{("buh", "/Отчёты/Квартальные/2026"), ("public", "/")},
		)
		_c, rows = share_reports.share_access({"only_not_working": 1})
		self.assertEqual({r["login"] for r in rows}, before | {"CORP\\Ivanov"})

	# ---------------------------------------------------------------- API

	def request(self, data: bytes):
		frappe.local.request = SimpleNamespace(get_data=lambda: data)

	def test_upload_api(self):
		user = "nas-collector@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "NAS", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("1C Sync")
		frappe.set_user(user)
		self.request(gzip.compress(collector_output().encode()))
		result = api.upload(SERVER)
		self.assertEqual(result["status"], "Успех")
		self.assertEqual(result["stats"]["folders"], 6)
		self.assertRaises(frappe.DoesNotExistError, api.upload, "NAS99")

	def test_upload_api_requires_role(self):
		user = "share-viewer@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "Viewer", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("Access Catalog Viewer")
		frappe.set_user(user)
		self.request(collector_output().encode())
		self.assertRaises(frappe.PermissionError, api.upload, SERVER)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Folder ACL"), 0)

	def test_server_code_is_validated(self):
		doc = frappe.get_doc({"doctype": "File Server", "server_code": "NAS 02", "title": "x"})
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_registry_app(self):
		from access_registry.registry import api as app

		self.assertSuccess(self.upload())
		control = app.control("shares")
		self.assertTrue(
			any(r["issue"] == "запрет" and r["ref_doctype"] == "Folder ACL" for r in control["rows"])
		)
		person = frappe.db.get_value("AD Account", self.account(2), "person")
		shares = app.person(person)["shares"]
		self.assertIn(
			{"share_name": "buh", "path": "/Зарплата", "level": "Изменение"},
			[{k: s[k] for k in ("share_name", "path", "level")} for s in shares],
		)
		self.assertIn(SERVER, [s["name"] for s in app.sources() if s["doctype"] == "File Server"])
