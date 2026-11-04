"""Tests of the Synology shared folder mirror: collector output, AD resolution, effective access, findings."""

import base64
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
from access_registry.file_shares.sync import Principals, run_upload
from access_registry.file_shares.synology import ParseError, access_level, decode_upload, parse
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


def without_folder(text: str, path: str) -> str:
	"""The collector output without one DIR block (the folder and its ACL lines)."""
	encoded = base64.b64encode(path.encode()).decode()
	lines, skip = [], False
	for line in text.splitlines():
		if line.startswith("DIR "):
			skip = line.split()[2] == encoded
		elif not line.startswith("ACL "):
			skip = False
		if not skip:
			lines.append(line)
	return "\n".join(lines) + "\n"


def without_share(text: str, name: str) -> str:
	"""The collector output without the SHARE block of one share and all its folder blocks."""
	encoded = base64.b64encode(name.encode()).decode()
	lines, skip = [], False
	for line in text.splitlines():
		if line.startswith(("SHARE ", "DIR ")):
			skip = line.split()[1] == encoded
		elif line == "END":
			skip = False
		if not skip:
			lines.append(line)
	return "\n".join(lines) + "\n"


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
		# the quick daily walk (STOP_ON_SAME=1) does not go inside /Отчёты: what the weekly full walk found
		# there stays
		stats = self.assertSuccess(self.upload(collector_output(1)))
		self.assertNotIn("Folder ACL: пропало", stats)
		self.assertEqual(stats["Folder ACL: быстрый обход не заходил, оставлено"], 1)
		self.assertEqual(self.folder("buh", "/Отчёты/Квартальные/2026").missing_in_source, 0)
		# a full walk without the folder: it is gone
		stats = self.assertSuccess(
			self.upload(without_folder(collector_output(0), "/Отчёты/Квартальные/2026"))
		)
		self.assertEqual(stats["Folder ACL: пропало"], 1)
		self.assertEqual(self.folder("buh", "/Отчёты/Квартальные/2026").missing_in_source, 1)
		self.assertSuccess(self.upload())
		self.assertEqual(self.folder("buh", "/Отчёты/Квартальные/2026").missing_in_source, 0)

	def test_guard_stops_big_shrink(self):
		self.assertSuccess(self.upload())
		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 5)
		frappe.db.set_single_value("Access Registry Settings", "shrink_threshold_pct", 10)
		frappe.clear_document_cache("Access Registry Settings", "Access Registry Settings")
		self.assertSuccess(self.upload(collector_output(1)))  # the quick walk loses nothing
		log = self.upload(without_folder(collector_output(0), "/Отчёты/Квартальные/2026"))
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

	def test_deny_is_weighed_right_by_right(self):
		self.assertSuccess(self.upload())
		access = ShareAccess()
		petrova = self.account(2)
		entry = lambda allow, rights, inherited=0: frappe._dict(  # noqa: E731
			ad_account=petrova,
			principal="CORP\\petrova",
			allow="Разрешить" if allow else "Запретить",
			rights=rights,
			inherited=inherited,
			level="",
		)
		level = lambda entries: access.folder_access(None, entries, None).get(petrova, ("—",))[0]  # noqa: E731
		self.assertEqual(level([entry(True, "rwxpdDaARWc--"), entry(False, "-w-pdD-------")]), "Чтение")
		self.assertEqual(level([entry(True, "rwxpdDaARWc--"), entry(False, "rwxpdDaARWcCo", 1)]), "Изменение")
		self.assertEqual(level([entry(True, "rwxpdDaARWc--", 1), entry(False, "rwxpdDaARWcCo")]), "—")

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
		self.assertEqual(result["stats"]["sender"], user)
		self.assertRaises(frappe.DoesNotExistError, api.upload, "NAS99")

	def test_upload_api_only_from_the_technical_user_of_the_server(self):
		user = "nas-collector@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "NAS", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("1C Sync")
		frappe.db.set_value("File Server", SERVER, "api_user", "Administrator")
		frappe.set_user(user)
		self.request(collector_output().encode())
		self.assertRaises(frappe.PermissionError, api.upload, SERVER)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Folder ACL"), 0)

	def test_gzip_bomb_is_refused(self):
		bomb = gzip.compress(b"#REGISTRY-SYNOLOGY 1\n" + b" " * 4096)
		self.assertRaises(ParseError, decode_upload, bomb, 1024)
		self.assertRaises(ParseError, decode_upload, bomb[:-8])
		self.assertTrue(decode_upload(bomb, 8192).startswith("#REGISTRY-SYNOLOGY"))

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

	# ---------------------------------------------------------------- parser: malformed input

	def test_parse_malformed_lines(self):
		broken_dir = collector_output().replace("DIR YnVo Lw== 0", "DIR YnVo Lw==")
		self.assertRaises(ParseError, parse, broken_dir)
		bad_b64 = collector_output().replace("DIR YnVo Lw== 0", "DIR !bad! Lw== 0")
		self.assertRaises(ParseError, parse, bad_b64)

	def test_decode_upload_accepts_plain_text_and_bad_utf8(self):
		self.assertEqual(decode_upload(b"#REGISTRY-SYNOLOGY 1"), "#REGISTRY-SYNOLOGY 1")
		self.assertEqual(decode_upload(b"\xff\xfe"), "\ufffd\ufffd")  # invalid utf-8 is replaced, not fatal

	def test_collector_errors_and_server_mismatch_are_warned(self):
		failure = base64.b64encode(b"synoacltool: read failed").decode()
		text = collector_output().replace("SERVER TkFTMDE=", "SERVER WFhU") + f"ERROR YnVo {failure}\n"
		log = self.upload(text)
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertIn("в выгрузке SERVER_CODE=XXT, а загружается в сервер NAS01", log.messages)
		self.assertIn("сборщик: buh synoacltool: read failed", log.messages)

	def test_unread_acl_becomes_an_issue(self):
		text = collector_output().replace(
			"ACL \t [0] user:CORP\\uvolen:allow:rwxpdDaARWc--:fd-- (level:0)",
			"ACL synoacltool error: Permission denied\n"
			"ACL this line is ignored\n"
			"ACL \t [0] user:CORP\\uvolen:allow:rwxpdDaARWc--:fd-- (level:0)",
		)
		self.assertSuccess(self.upload(text))
		self.assertIn(
			"сборщик не прочитал права: synoacltool error: Permission denied",
			self.folder("buh", "/Старое").issues,
		)

	def test_principals_resolution(self):
		principals = Principals(frappe.get_doc("File Server", SERVER))
		gg_buh = f"{DOMAIN}:{GRP(9)}"

		everyone = principals.resolve("everyone", "")
		self.assertEqual((everyone["principal"], everyone["principal_type"]), ("Все", "Все"))
		auth = principals.resolve("authenticated_user", "")
		self.assertEqual((auth["principal"], auth["principal_type"]), ("Прошедшие проверку", "Все"))
		owner = principals.resolve("owner", "")
		self.assertEqual((owner["principal"], owner["principal_type"]), ("Владелец", "Владелец"))

		by_upn = principals.resolve("user", "petrova@corp.example.local")
		self.assertEqual(
			(by_upn["principal_type"], by_upn["ad_account"]), ("Пользователь AD", self.account(2))
		)
		self.assertEqual(
			principals.resolve("user", "PETROVA@CORP.EXAMPLE.LOCAL")["ad_account"], self.account(2)
		)
		self.assertEqual(
			principals.resolve("user", "ghost@corp.example.local")["principal_type"], "Не найден в AD"
		)

		direct = principals.resolve("user", "CORP\\PETROVA")
		self.assertEqual(
			(direct["principal_type"], direct["ad_account"]), ("Пользователь AD", self.account(2))
		)
		self.assertEqual(
			principals.resolve("group", "OTHER\\GG_Buh_Share")["principal_type"], "Не найден в AD"
		)
		group = principals.resolve("group", "corp\\gg_buh_share")
		self.assertEqual((group["principal_type"], group["ad_group"]), ("Группа AD", gg_buh))

		self.assertEqual(principals.resolve("user", "backup")["principal_type"], "Локальный пользователь")
		self.assertEqual(principals.resolve("group", "operators")["principal_type"], "Локальная группа")

	# ---------------------------------------------------------------- import: edge cases

	def test_failed_upload_is_logged_and_updates_server_status(self):
		log = self.upload("not a collector output")
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("Выгрузка не разобрана", log.messages)
		stats = json.loads(log.stats)
		self.assertEqual((stats["server"], stats["sender"]), (SERVER, frappe.session.user))
		server = frappe.get_doc("File Server", SERVER)
		self.assertTrue(server.last_status.startswith("Ошибка"))
		self.assertFalse(server.last_upload)
		self.assertEqual((server.shares_count or 0, server.folders_count or 0), (0, 0))
		self.assertEqual(frappe.db.count("Folder ACL"), 0)

	def test_upload_log_contains_no_personal_data(self):
		log = self.upload()
		self.assertEqual(log.status, "Успех", log.messages)
		person = frappe.db.get_value("AD Account", self.account(1), "person")
		full_name = frappe.db.get_value("Person", person, "full_name")
		self.assertTrue(full_name)
		self.assertNotIn(full_name, log.messages)  # personal names never reach the Sync Log
		self.assertNotIn(full_name, log.stats or "")

	def test_share_gone_is_marked_missing_and_restored(self):
		self.assertSuccess(self.upload())
		stats = self.assertSuccess(self.upload(without_share(collector_output(), "public")))
		self.assertEqual(stats.get("File Share: пропало"), 1)
		self.assertEqual(stats.get("Folder ACL: пропало"), 1)
		self.assertEqual(frappe.get_doc("File Share", f"{SERVER}:public").missing_in_source, 1)
		self.assertEqual(self.folder("public", "/").missing_in_source, 1)
		_c, issues = share_reports.share_issues({"server": SERVER})
		self.assertNotIn("public", {i["share_name"] for i in issues})
		stats = self.assertSuccess(self.upload())  # a full walk finds the share again
		self.assertEqual(stats.get("File Share: изменено"), 1)  # same record, the flag is just cleared
		self.assertEqual(stats.get("Folder ACL: изменено"), 1)
		self.assertEqual(frappe.get_doc("File Share", f"{SERVER}:public").missing_in_source, 0)
		self.assertEqual(self.folder("public", "/").missing_in_source, 0)

	def test_deep_folders_are_kept_when_the_walk_is_shallow(self):
		"""A walk with a smaller MAX_DEPTH cannot prove deep folders gone: they are kept for a full walk."""
		self.assertSuccess(self.upload())
		shallow = without_folder(collector_output(), "/Отчёты/Квартальные/2026").replace(
			"MAX_DEPTH 12", "MAX_DEPTH 1"
		)
		stats = self.assertSuccess(self.upload(shallow))
		self.assertEqual(stats.get("Folder ACL: глубже MAX_DEPTH, не проверено"), 1)
		self.assertNotIn("Folder ACL: пропало", stats)
		self.assertEqual(self.folder("buh", "/Отчёты/Квартальные/2026").missing_in_source, 0)

	def test_guard_does_not_trip_below_min_records(self):
		self.assertSuccess(self.upload())
		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 100)
		frappe.db.set_single_value("Access Registry Settings", "shrink_threshold_pct", 10)
		frappe.clear_document_cache("Access Registry Settings", "Access Registry Settings")
		# two of six folders disappear (33% > 10%), but the registry is smaller than the guard minimum
		text = without_folder(without_folder(collector_output(), "/Старое"), "/Архив")
		stats = self.assertSuccess(self.upload(text))
		self.assertEqual(stats.get("Folder ACL: пропало"), 2)
		self.assertEqual(frappe.db.count("Folder ACL", {"server": SERVER, "missing_in_source": 1}), 2)

	# ---------------------------------------------------------------- effective access: edge cases

	def test_share_deny_wins_over_acl_allow(self):
		"""Deny (NA) in the DSM privilege list removes the account even when the folder ACL allows him."""
		frappe.db.set_value("AD Account", self.account(3), "enabled", 1)  # sidorov is disabled in the fixture
		text = (
			collector_output()
			.replace(
				"RW list ....[@CORP\\GG_Buh_Share,admin]",
				"RW list ....[@CORP\\GG_Buh_Share,admin,CORP\\sidorov]",
			)
			.replace(
				"ACL \t [1] group:administrators:allow:rwxpdDaARWcCo:fd-- (level:0)\nDIR YnVo L9CQ0YDRhdC40LI=",
				"ACL \t [1] group:administrators:allow:rwxpdDaARWcCo:fd-- (level:0)\n"
				"ACL \t [2] user:CORP\\sidorov:allow:rwxpdDaARWcCo:fd-- (level:0)\n"
				"DIR YnVo L9CQ0YDRhdC40LI=",
			)
		)
		self.assertSuccess(self.upload(text))
		self.assertNotIn("sidorov", self.access("buh", "/"))
		allowed = text.replace("NA list ....[CORP\\sidorov]", "NA list ....[]")
		self.assertSuccess(self.upload(allowed))
		self.assertEqual(self.access("buh", "/")["sidorov"], "Изменение")  # RW caps his full ACL access

	def test_folder_access_falls_back_to_level_when_rights_are_missing(self):
		"""Entries stored before the rights string was kept derive their decision from the level alone."""
		access = ShareAccess()
		petrova = self.account(2)

		def entry(allow, level):
			return frappe._dict(
				ad_account=petrova,
				principal="CORP\\petrova",
				allow="Разрешить" if allow else "Запретить",
				rights="",
				inherited=0,
				level=level,
			)

		result = access.folder_access(None, [entry(True, "Полный доступ")], None)
		self.assertEqual(result[petrova], ("Полный доступ", "CORP\\petrova"))
		self.assertNotIn(petrova, access.folder_access(None, [entry(False, "Запрет")], None))

	def test_unlinked_account_is_reported_as_not_bound(self):
		"""An AD account not linked to a Person shows up in the report with a «не привязан» status."""
		data = ad_with_nested_group()
		zup_users = next(g for g in data["groups"] if g["sAMAccountName"] == "GG_1C_ZUP_Users")
		svc_backup = next(u for u in data["users"] if u["objectGUID"] == ACC(6))
		svc_backup.setdefault("memberOf", []).append(zup_users["distinguishedName"])
		log = run_domain_sync(DOMAIN, commit=False, fetch=lambda _d: data)
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertSuccess(self.upload())
		_c, rows = share_reports.share_access({"server": SERVER, "share": f"{SERVER}:public"})
		row = next(r for r in rows if r["login"] == "CORP\\svc_backup")
		self.assertFalse(row["person"])
		self.assertEqual(row["person_status"], "не привязан")
		self.assertEqual(row["full_name"], "svc_backup")  # falls back to the AD display name
		# «не привязан» ≠ «неработающий»: only_not_working lists accounts of bound persons
		# whose status is not «Работает»; unlinked accounts stay out (same rule as in share_issues)
		_c, not_working = share_reports.share_access({"only_not_working": 1})
		self.assertNotIn("CORP\\svc_backup", {r["login"] for r in not_working})

	# ---------------------------------------------------------------- reports: filters

	def test_share_access_path_filter(self):
		self.assertSuccess(self.upload())
		_c, rows = share_reports.share_access({"path": "Квартальные"})
		self.assertTrue(rows)
		self.assertTrue(all("Квартальные" in (r["path"] or "") for r in rows))
		_c, none = share_reports.share_access({"path": "no-such-folder"})
		self.assertEqual(none, [])

	# ---------------------------------------------------------------- API: permissions

	def test_upload_api_rejects_empty_body(self):
		user = "nas-collector@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "NAS", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("1C Sync")
		frappe.set_user(user)
		self.request(b"")
		self.assertRaises(frappe.ValidationError, api.upload, SERVER)
		frappe.local.request = None
		self.assertRaises(frappe.ValidationError, api.upload, SERVER)  # no request at all

	def test_upload_api_administrator_passes_sender_binding(self):
		user = "nas-collector@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "NAS", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("1C Sync")
		frappe.db.set_value("File Server", SERVER, "api_user", user)
		frappe.set_user("Administrator")
		self.request(collector_output().encode())
		result = api.upload(SERVER)
		self.assertEqual(result["status"], "Успех")
		self.assertEqual(result["stats"]["sender"], "Administrator")

	def test_load_file_requires_administrator(self):
		user = "share-viewer@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "Viewer", "send_welcome_email": 0}
			).insert()
		frappe.get_doc("User", user).add_roles("Access Catalog Viewer")
		frappe.set_user(user)
		server = frappe.get_doc("File Server", SERVER)
		self.assertRaises(frappe.PermissionError, server.load_file)  # a read role is not enough
		frappe.set_user("Administrator")
		server = frappe.get_doc("File Server", SERVER)
		self.assertRaises(frappe.ValidationError, server.load_file)  # nothing is attached yet
