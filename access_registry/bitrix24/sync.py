"""Mirror of a Bitrix24 portal: users, structure, workgroups, absences and access rights.

Reading goes through the REST API (``client.py``) and, for an on-premise portal, the export script
``bitrix24/registry_export.php``. After the mirror is saved, the registry writes the middle name,
the birth date and the employee UUID from the HR data into user profiles, if the portal allows it
(``write_enabled``). Every write is logged in «B24 Write Log».
"""

import hashlib
import json
import traceback
from collections import Counter, defaultdict

import frappe
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

from access_registry.access_catalog.importer import save_changed, sync_child_table
from access_registry.access_catalog.linking import NameMatcher
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user
from access_registry.sync.normalize import normalize_name

SAVEPOINT = "bitrix24_sync"
DERIVED_VERSION = 1
LOG_KIND = "Битрикс24"

BY_UUID = "UUID в Битрикс24"
BY_AD = "Учётка AD"
BY_NAME = "ФИО"
BY_SHORT_NAME = "Фамилия и имя"
BY_HAND = "Вручную"

ROLE_TITLES = {"A": "Владелец", "E": "Модератор", "K": "Участник", "Z": "Запрос"}
ADMIN_GROUP = 1  # Bitrix: group 1 — administrators
SKIPPED_GROUPS = {2}  # «Все пользователи»
CRM_ENTITIES = {
	"LEAD": "Лиды",
	"DEAL": "Сделки",
	"CONTACT": "Контакты",
	"COMPANY": "Компании",
	"QUOTE": "Предложения",
	"INVOICE": "Счета (старые)",
	"SMART_INVOICE": "Счета",
	"ORDER": "Заказы",
	"CONFIG": "Настройки CRM",
	"WEBFORM": "Веб-формы",
	"BUTTON": "Виджеты на сайт",
	"SALETARGET": "План продаж",
	"EXCLUSION": "Исключения",
}
CRM_ACTIONS = {
	"READ": "Чтение",
	"ADD": "Добавление",
	"WRITE": "Изменение",
	"DELETE": "Удаление",
	"EXPORT": "Экспорт",
	"IMPORT": "Импорт",
	"AUTOMATION": "Роботы",
}
CRM_LEVELS = {
	"A": "свои",
	"D": "свои и отдела",
	"F": "свои, отдела и подотделов",
	"O": "все открытые",
	"X": "все",
}
DISK_TASKS = {
	"disk_access_read": "Чтение",
	"disk_access_add": "Добавление",
	"disk_access_edit": "Изменение",
	"disk_access_full": "Полный доступ",
	"disk_access_sharing": "Изменение и доступ",
}


class B24GuardTripped(frappe.ValidationError):
	pass


# --------------------------------------------------------------------------- jobs


def scheduled_sync():
	for portal in frappe.get_all("B24 Portal", filters={"enabled": 1}, pluck="name"):
		enqueue(portal)


def enqueue(portal: str):
	return frappe.enqueue(
		"access_registry.bitrix24.sync.run_portal_sync",
		queue="long",
		timeout=get_settings().job_timeout,
		job_id=f"bitrix24_sync::{portal}",
		deduplicate=True,
		portal=portal,
	)


def make_client(portal):
	from access_registry.bitrix24.client import B24Client

	return B24Client(
		portal.get_password("webhook"),
		timeout=cint(get_settings().http_timeout) or 60,
		verify_ssl=bool(portal.verify_ssl),
		min_interval=flt(portal.request_interval),
	)


def fetch_portal(portal, client) -> dict:
	"""Everything the mirror needs, as plain dicts (the shape of the tests' fixture)."""
	groups = client.workgroups()
	data = {
		"users": client.users(),
		"departments": client.departments(),
		"workgroups": groups,
		"workgroup_members": client.workgroup_members([g.get("ID") for g in groups]),
		"crm_types": client.crm_types(),
	}
	if portal.exporter_url:
		data["export"] = fetch_export(portal)
	return data


def fetch_export(portal) -> dict:
	import requests

	response = requests.get(
		portal.exporter_url,
		headers={"X-Registry-Token": portal.get_password("exporter_token", raise_exception=False) or ""},
		timeout=cint(get_settings().http_timeout) or 300,
		verify=bool(portal.verify_ssl),
	)
	if response.status_code != 200:
		raise frappe.ValidationError(
			f"Скрипт выгрузки ответил HTTP {response.status_code}: {response.text[:300]}"
		)
	return response.json()


def run_portal_sync(portal: str, commit: bool = True, fetch=None, client=None):
	"""Reads the portal, mirrors it in one transaction, then writes HR data to profiles.

	``fetch(portal_doc) -> dict`` and ``client`` replace the network in tests. Returns the Sync Log.
	"""
	settings = get_settings()
	log = frappe.get_doc(
		{"doctype": "Sync Log", "kind": LOG_KIND, "status": "В процессе", "started": now_datetime()}
	).insert(ignore_permissions=True)
	if commit:
		frappe.db.commit()
	stats, messages = {}, []
	previous_user = frappe.session.user
	_switch_user(settings.sync_user, messages)
	frappe.db.savepoint(SAVEPOINT)
	doc = frappe.get_doc("B24 Portal", portal)
	status = "Ошибка"
	try:
		if client is None and fetch is None:
			client = make_client(doc)
		data = fetch(doc) if fetch else fetch_portal(doc, client)
		importer = PortalImport(doc)
		stats = importer.run(data)
		messages += importer.warnings
		frappe.db.set_value("B24 Portal", portal, "last_sync", now_datetime(), update_modified=False)
		status = "Успех"
	except B24GuardTripped as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except Exception:
		messages.append(traceback.format_exc())
		frappe.db.rollback(save_point=SAVEPOINT)
	if status == "Успех" and doc.write_enabled:
		if commit:
			frappe.db.commit()
		writer = ProfileWriter(doc, client, log.name, commit=commit)
		try:
			stats.update(writer.run())
		except Exception:
			messages.append("Запись в Битрикс24 прервана:\n" + traceback.format_exc())
		messages += writer.warnings
	_switch_user(previous_user, None)
	log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps({"portal": portal, **stats}, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	log.save(ignore_permissions=True)
	frappe.db.set_value(
		"B24 Portal",
		portal,
		"last_status",
		f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})",
		update_modified=False,
	)
	if commit:
		frappe.db.commit()
	return log


def load_portal_file(portal: str, path: str):
	"""Loads a saved portal dump (JSON of the fetch_portal shape) instead of calling Bitrix24.

	For a dev site and synthetic data (tools/generate_synthetic.py --bitrix24). Writes are not
	sent anywhere: without a client the write step only reports what it would change.
	"""
	frappe.only_for("System Manager")
	with open(path, encoding="utf-8") as fh:
		data = json.load(fh)
	log = run_portal_sync(portal, fetch=lambda _portal: data, client=DryRunClient())
	return {"status": log.status, "log": log.name}


class DryRunClient:
	"""Accepts writes without sending them (dev sites and file loads)."""

	def __init__(self):
		self.updates = []

	def update_user(self, user_id, fields):
		self.updates.append((user_id, fields))
		return True


# --------------------------------------------------------------------------- helpers


def src_hash(payload) -> str:
	return hashlib.md5(
		json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
	).hexdigest()


def b24_date(value):
	"""Bitrix24 date or datetime («1980-01-15T03:00:00+03:00», «15.01.1980») → date or None."""
	if not value:
		return None
	text = str(value).strip()
	try:
		if len(text) >= 10 and text[4] == "-":
			return getdate(text[:10])
		if len(text) >= 10 and text[2] == ".":
			return getdate(f"{text[6:10]}-{text[3:5]}-{text[0:2]}")
	except Exception:
		return None
	return None


def b24_datetime(value):
	if not value:
		return None
	try:
		dt = get_datetime(str(value))
	except Exception:
		return None
	if dt and dt.tzinfo:
		dt = dt.replace(tzinfo=None)
	return dt


def as_bool(value) -> int:
	if isinstance(value, str):
		return int(value.strip().upper() in ("Y", "TRUE", "1"))
	return int(bool(value))


def ids(value) -> list[str]:
	if value in (None, "", False):
		return []
	if not isinstance(value, list | tuple):
		value = [value]
	return [str(v) for v in value if str(v) not in ("", "0")]


def full_name(last, first, second) -> str:
	return " ".join(x.strip() for x in (last or "", first or "", second or "") if x and x.strip())


class PersonResolver:
	"""Bitrix24 user → employee. Order: manual (controller), UUID field, AD account, full name,
	surname and first name (when the middle name is empty — the case the registry fills in)."""

	def __init__(self, uuid_field: str | None):
		self.uuid_field = uuid_field
		self.persons = {
			p.name: p
			for p in frappe.get_all("Person", fields=["name", "status", "name_key"], limit_page_length=0)
		}
		self.names = NameMatcher()
		self.short = defaultdict(list)
		for p in self.persons.values():
			parts = (p.name_key or "").split(" ")
			if len(parts) >= 2:
				self.short[" ".join(parts[:2])].append(p)
		self.by_mail, self.by_login = {}, {}
		logins = Counter()
		for a in frappe.get_all(
			"AD Account",
			filters={"missing_in_source": 0},
			fields=["name", "person", "mail", "user_principal_name", "sam_account_name"],
			limit_page_length=0,
		):
			for mail in (a.mail, a.user_principal_name):
				if mail:
					self.by_mail[mail.lower()] = a
			if a.sam_account_name:
				key = a.sam_account_name.lower()
				logins[key] += 1
				self.by_login[key] = a
		for key, count in logins.items():
			if count > 1:  # the same login in several domains: ambiguous
				self.by_login.pop(key, None)

	def ad_account(self, row, login):
		login = (login or "").lower()
		if "\\" in login:
			login = login.split("\\", 1)[1]
		if "@" in login:
			account = self.by_mail.get(login)
			if account:
				return account
			login = login.split("@", 1)[0]
		if login and login in self.by_login:
			return self.by_login[login]
		email = (row.get("EMAIL") or "").strip().lower()
		return self.by_mail.get(email) if email else None

	def resolve(self, row, login) -> tuple:
		"""Returns (person, method, note, ad_account)."""
		account = self.ad_account(row, login)
		value = (str(row.get(self.uuid_field) or "") if self.uuid_field else "").strip().lower()
		if value and value in self.persons:
			return value, BY_UUID, "", account and account.name
		if account and account.person:
			return account.person, BY_AD, "", account.name
		last, first, second = row.get("LAST_NAME"), row.get("NAME"), row.get("SECOND_NAME")
		if second:
			person, note = self.names.match(full_name(last, first, second))
			return person, BY_NAME if person else "", note, account and account.name
		key = normalize_name(full_name(last, first, ""))
		candidates = self.short.get(key, []) if len(key.split(" ")) == 2 else []
		working = [c for c in candidates if c.status == "Работает"]
		if len(candidates) == 1:
			return candidates[0].name, BY_SHORT_NAME, "", account and account.name
		if len(working) == 1:
			return working[0].name, BY_SHORT_NAME, "", account and account.name
		if candidates:
			return (
				None,
				"",
				f"отчества нет, сотрудников «{key}» несколько ({len(candidates)})",
				account and account.name,
			)
		return None, "", f"сотрудник «{full_name(last, first, second)}» не найден", account and account.name


# --------------------------------------------------------------------------- import


class PortalImport:
	def __init__(self, portal):
		self.portal = portal
		self.code = portal.name
		self.settings = get_settings()
		self.counters = Counter()
		self.warnings: list[str] = []

	def warn(self, text):
		self.warnings.append(f"Предупреждение: {text}")

	def key(self, b24_id) -> str:
		return f"{self.code}:{b24_id}"

	def run(self, data: dict) -> dict:
		users = [u for u in data.get("users") or [] if u.get("ID")]
		departments = [d for d in data.get("departments") or [] if d.get("ID")]
		export = data.get("export") or {}
		self.check_guard(len(users))
		self.user_ids = {str(u["ID"]) for u in users}
		self.dept_rows = {str(d["ID"]): d for d in departments}

		self.import_departments(departments)
		self.import_users(users, export)
		self.link_departments(departments)
		self.import_workgroups(data.get("workgroups") or [], data.get("workgroup_members") or {})
		if export:
			self.import_absences(export.get("absences") or [])
			self.import_grants(export, data.get("crm_types") or [])
			for warning in export.get("warnings") or []:
				self.warn(f"скрипт выгрузки: {warning}")
		elif self.portal.exporter_url:
			self.warn("скрипт выгрузки не вернул данных")

		self.counters["users"] = len(users)
		self.counters["departments"] = len(departments)
		self.counters["workgroups"] = len(data.get("workgroups") or [])
		self.counters["smart_processes"] = len(data.get("crm_types") or [])
		return dict(self.counters)

	def check_guard(self, received):
		threshold = cint(self.settings.shrink_threshold_pct)
		actual = frappe.db.count("B24 User", {"portal": self.code, "missing_in_source": 0})
		if actual < cint(self.settings.guard_min_records):
			return
		if received < actual * (1 - threshold / 100):
			shrink = round(100 * (actual - received) / actual, 1)
			raise B24GuardTripped(
				f"Загрузка портала {self.code} остановлена предохранителем: пользователей в Битрикс24 {received}, "
				f"актуальных в реестре {actual} — сокращение на {shrink}%, допустимо не более {threshold}%. "
				f"Проверьте права вебхука (нужен администратор). Если сокращение ожидаемое, временно поднимите "
				f"«Допустимое сокращение выгрузки, %» до {int(shrink) + 1} или выше."
			)

	# ------------------------------------------------------------ generic upsert

	def load(self, doctype, uid, payload_hash):
		if frappe.db.exists(doctype, uid):
			current = frappe.db.get_value(doctype, uid, ["src_hash", "missing_in_source"], as_dict=True)
			if current.src_hash == payload_hash and not current.missing_in_source:
				self.counters[f"{doctype}: без изменений"] += 1
				return None
			return frappe.get_doc(doctype, uid)
		doc = frappe.new_doc(doctype)
		doc.uid = uid
		return doc

	def save(self, doc):
		new = doc.is_new()
		doc.portal = self.code
		doc.missing_in_source = 0
		if not save_changed(doc):
			self.counters[f"{doc.doctype}: без изменений"] += 1
		else:
			self.counters[f"{doc.doctype}: {'создано' if new else 'изменено'}"] += 1

	def mark_missing(self, doctype, seen) -> int:
		count = 0
		for name in frappe.get_all(
			doctype, filters={"portal": self.code, "missing_in_source": 0}, pluck="name"
		):
			if name not in seen:
				doc = frappe.get_doc(doctype, name)
				doc.missing_in_source = 1
				doc.save(ignore_permissions=True, ignore_version=False)
				count += 1
		if count:
			self.counters[f"{doctype}: пропало"] += count
		return count

	# ------------------------------------------------------------ departments

	def depth(self, dept_id, seen=None) -> int:
		seen = seen or set()
		row = self.dept_rows.get(dept_id)
		parent = str(row.get("PARENT") or "") if row else ""
		if not parent or parent in seen or parent not in self.dept_rows:
			return 0
		seen.add(dept_id)
		return 1 + self.depth(parent, seen)

	def import_departments(self, departments):
		seen = set()
		for row in sorted(departments, key=lambda r: self.depth(str(r["ID"]))):
			uid = self.key(row["ID"])
			seen.add(uid)
			parent = str(row.get("PARENT") or "")
			parent_uid = self.key(parent) if parent in self.dept_rows else None
			head = str(row.get("UF_HEAD") or "")
			payload = {"d": row, "v": DERIVED_VERSION}
			h = src_hash(payload)
			doc = self.load("B24 Department", uid, h)
			if doc is None:
				continue
			doc.b24_id = str(row["ID"])
			doc.department_name = row.get("NAME") or uid
			doc.parent_department = (
				parent_uid if parent_uid and frappe.db.exists("B24 Department", parent_uid) else None
			)
			# head: the user may not be imported yet — link_departments() completes it
			doc.head = self.key(head) if head and frappe.db.exists("B24 User", self.key(head)) else None
			doc.src_hash = h
			self.save(doc)
		self.mark_missing("B24 Department", seen)

	def link_departments(self, departments):
		"""After users: heads, their employees, member counts and the match with HR departments."""
		members = Counter()
		for dept in frappe.db.sql(
			"""select d.department from `tabB24 User Department` d
			join `tabB24 User` u on u.name = d.parent
			where u.portal = %s and u.active = 1 and u.missing_in_source = 0""",
			self.code,
		):
			members[dept[0]] += 1
		hr_index = defaultdict(list)
		for row in frappe.get_all(
			"HR Department",
			filters={"missing": 0, "node_type": "Подразделение"},
			fields=["name", "title", "parent_hr_department", "head"],
			limit_page_length=0,
		):
			hr_index[normalize_name(row.title)].append(row)
		person_of = dict(
			frappe.get_all(
				"B24 User",
				filters={"portal": self.code},
				fields=["name", "person"],
				as_list=True,
				limit_page_length=0,
			)
		)
		mapped = {}
		for row in sorted(departments, key=lambda r: self.depth(str(r["ID"]))):
			uid = self.key(row["ID"])
			current = frappe.db.get_value(
				"B24 Department",
				uid,
				[
					"parent_department",
					"head",
					"head_person",
					"member_count",
					"hr_department",
					"hr_link_method",
					"hr_link_note",
					"manual_hr_department",
				],
				as_dict=True,
			)
			if not current:
				continue
			parent = str(row.get("PARENT") or "")
			head = str(row.get("UF_HEAD") or "")
			values = {
				"parent_department": self.key(parent) if parent in self.dept_rows else None,
				"head": self.key(head) if head and head in self.user_ids else None,
				"member_count": members.get(uid, 0),
			}
			values["head_person"] = person_of.get(values["head"]) if values["head"] else None
			if current.manual_hr_department:
				hr, method, note = current.manual_hr_department, BY_HAND, ""
			else:
				hr, method, note = self.match_hr(row, hr_index, mapped.get(values["parent_department"]))
			mapped[uid] = hr
			values.update({"hr_department": hr, "hr_link_method": method, "hr_link_note": note})
			changed = {
				k: v
				for k, v in values.items()
				if (current.get(k) or None) != (v or None)
				and not (k == "member_count" and cint(current.get(k)) == cint(v))
			}
			if changed:
				frappe.db.set_value("B24 Department", uid, changed, update_modified=False)

	def match_hr(self, row, hr_index, parent_hr):
		candidates = hr_index.get(normalize_name(row.get("NAME")), [])
		if len(candidates) == 1:
			return candidates[0].name, "Название", ""
		if len(candidates) > 1 and parent_hr:
			under = [c for c in candidates if c.parent_hr_department == parent_hr]
			if len(under) == 1:
				return under[0].name, "Название", ""
		if candidates:
			return None, "", f"в кадрах несколько подразделений «{row.get('NAME')}» ({len(candidates)})"
		return None, "", "в кадрах нет подразделения с таким названием"

	# ------------------------------------------------------------ users

	def import_users(self, users, export):
		logins = {str(x.get("user_id")): x for x in export.get("logins") or []}
		admins = set()
		for group in export.get("user_groups") or []:
			if cint(group.get("id")) == ADMIN_GROUP:
				admins = {str(m) for m in group.get("members") or []}
		resolver = PersonResolver(self.portal.person_uuid_field)
		manual = dict(
			frappe.get_all(
				"B24 User",
				filters={"portal": self.code, "manual_person": ["is", "set"]},
				fields=["name", "manual_person"],
				as_list=True,
			)
		)
		seen = set()
		for row in users:
			uid = self.key(row["ID"])
			seen.add(uid)
			info = logins.get(str(row["ID"]), {})
			login = info.get("login") or ""
			person, method, note, ad_account = resolver.resolve(row, login)
			if uid in manual:
				person, method, note = manual[uid], BY_HAND, ""
			departments = [
				{"department": self.key(d), "department_name": (self.dept_rows.get(d) or {}).get("NAME") or d}
				for d in ids(row.get("UF_DEPARTMENT"))
				if d in self.dept_rows
			]
			uuid_value = (
				str(row.get(self.portal.person_uuid_field) or "") if self.portal.person_uuid_field else ""
			)
			fields = {
				"full_name": full_name(row.get("LAST_NAME"), row.get("NAME"), row.get("SECOND_NAME"))
				or row.get("EMAIL")
				or uid,
				"last_name": row.get("LAST_NAME") or "",
				"first_name": row.get("NAME") or "",
				"second_name": row.get("SECOND_NAME") or "",
				"birthday": b24_date(row.get("PERSONAL_BIRTHDAY")),
				"email": row.get("EMAIL") or "",
				"login": login,
				"active": as_bool(row.get("ACTIVE")),
				"user_type": row.get("USER_TYPE") or "",
				"is_admin": int(str(row["ID"]) in admins),
				"work_position": row.get("WORK_POSITION") or "",
				"last_login": b24_datetime(row.get("LAST_LOGIN")),
				"date_register": b24_datetime(row.get("DATE_REGISTER")),
				"person": person,
				"person_link_method": method,
				"person_link_note": note,
				"ad_account": ad_account,
				"uuid_in_portal": uuid_value,
				"xml_id": row.get("XML_ID") or info.get("xml_id") or "",
				"external_auth_id": info.get("external_auth_id") or "",
			}
			# last login changes all the time: stored, but kept out of the hash
			payload = {
				"f": {k: v for k, v in fields.items() if k != "last_login"},
				"d": departments,
				"v": DERIVED_VERSION,
			}
			h = src_hash(payload)
			doc = self.load("B24 User", uid, h)
			if doc is None:
				if frappe.db.get_value("B24 User", uid, "last_login") != fields["last_login"]:
					frappe.db.set_value(
						"B24 User", uid, "last_login", fields["last_login"], update_modified=False
					)
				continue
			doc.b24_id = str(row["ID"])
			doc.update(fields)
			sync_child_table(doc, "departments", ("department",), departments)
			doc.src_hash = h
			self.save(doc)
		self.mark_missing("B24 User", seen)

	# ------------------------------------------------------------ workgroups

	def import_workgroups(self, groups, members):
		names = dict(
			frappe.get_all(
				"B24 User",
				filters={"portal": self.code},
				fields=["name", "full_name"],
				as_list=True,
				limit_page_length=0,
			)
		)
		seen = set()
		for row in groups:
			uid = self.key(row["ID"])
			seen.add(uid)
			rows = []
			for m in members.get(str(row["ID"])) or []:
				user = self.key(m.get("USER_ID"))
				if user in names:
					rows.append(
						{
							"user": user,
							"user_name": names[user],
							"role": ROLE_TITLES.get(m.get("ROLE"), "Участник"),
						}
					)
			rows.sort(key=lambda r: (r["role"], r["user_name"] or ""))
			payload = {"g": row, "m": rows, "v": DERIVED_VERSION}
			h = src_hash(payload)
			doc = self.load("B24 Workgroup", uid, h)
			if doc is None:
				continue
			doc.b24_id = str(row["ID"])
			doc.group_name = row.get("NAME") or uid
			doc.is_project = as_bool(row.get("PROJECT"))
			doc.active = as_bool(row.get("ACTIVE", "Y"))
			doc.closed = as_bool(row.get("CLOSED"))
			doc.visible = as_bool(row.get("VISIBLE"))
			doc.opened = as_bool(row.get("OPENED"))
			owner = self.key(row.get("OWNER_ID")) if row.get("OWNER_ID") else None
			doc.owner_user = owner if owner in names else None
			sync_child_table(doc, "members", ("user",), rows)
			doc.member_count = len(rows)
			doc.src_hash = h
			self.save(doc)
		self.mark_missing("B24 Workgroup", seen)

	# ------------------------------------------------------------ absences

	def import_absences(self, absences):
		users = dict(
			frappe.get_all(
				"B24 User",
				filters={"portal": self.code},
				fields=["name", "person"],
				as_list=True,
				limit_page_length=0,
			)
		)
		seen = set()
		for row in absences:
			if not row.get("id"):
				continue
			uid = self.key(f"abs:{row['id']}")
			seen.add(uid)
			user = self.key(row.get("user_id"))
			payload = {"a": row, "person": users.get(user), "v": DERIVED_VERSION}
			h = src_hash(payload)
			doc = self.load("B24 Absence", uid, h)
			if doc is None:
				continue
			doc.b24_id = str(row["id"])
			doc.user = user if user in users else None
			doc.person = users.get(user)
			doc.date_from = b24_date(row.get("date_from"))
			doc.date_to = b24_date(row.get("date_to")) or doc.date_from
			doc.absence_type = row.get("type") or ""
			doc.title = row.get("title") or row.get("type") or uid
			doc.src_hash = h
			self.save(doc)
		self.mark_missing("B24 Absence", seen)

	# ------------------------------------------------------------ access rights

	def import_grants(self, export, crm_types):
		principals = PrincipalNames(self.code, export)
		smart = {str(t.get("entityTypeId")): t.get("title") for t in crm_types if t.get("entityTypeId")}
		grants = []
		roles = {cint(r.get("id")): r for r in export.get("crm_roles") or []}
		for relation in export.get("crm_role_relations") or []:
			role = roles.get(cint(relation.get("role_id")))
			if not role:
				continue
			by_entity = defaultdict(list)
			for p in role.get("permissions") or []:
				if p.get("level"):
					by_entity[p.get("entity") or ""].append(p)
			for entity, perms in sorted(by_entity.items()):
				kind, title = crm_resource(entity, smart)
				grants.append(
					{
						"resource_type": kind,
						"resource": title,
						"permission": "; ".join(
							f"{CRM_ACTIONS.get(p['action'], p['action'])}: {CRM_LEVELS.get(p['level'], p['level'])}"
							for p in sorted(perms, key=lambda p: p.get("action") or "")
						),
						"via": f"Роль CRM «{role.get('name')}»",
						"access_code": relation.get("access_code") or "",
					}
				)
		for right in export.get("disk_rights") or []:
			grants.append(
				{
					"resource_type": "Диск",
					"resource": f"{right.get('storage')}: {right.get('path') or '/'}",
					"permission": DISK_TASKS.get(right.get("task"), right.get("task") or ""),
					"via": "Права на папку",
					"access_code": right.get("access_code") or "",
					"negative": cint(right.get("negative")),
				}
			)
		for group in export.get("user_groups") or []:
			gid = cint(group.get("id"))
			if gid in SKIPPED_GROUPS or str(group.get("string_id") or "").upper().startswith("EMPLOYEES"):
				continue
			title = "Администраторы портала" if gid == ADMIN_GROUP else f"Группа «{group.get('name')}»"
			for member in group.get("members") or []:
				grants.append(
					{
						"resource_type": "Группа пользователей",
						"resource": title,
						"permission": "Полный доступ к порталу" if gid == ADMIN_GROUP else "Участник",
						"via": f"Группа пользователей {gid}",
						"access_code": f"U{member}",
					}
				)
		seen = set()
		for grant in grants:
			grant["principal"] = principals.title(grant["access_code"])
			grant.setdefault("negative", 0)
			uid = (
				self.code
				+ ":"
				+ hashlib.md5(
					json.dumps(
						[grant["resource_type"], grant["resource"], grant["via"], grant["access_code"]],
						ensure_ascii=False,
					).encode()
				).hexdigest()[:20]
			)
			if uid in seen:
				continue
			seen.add(uid)
			h = src_hash(grant)
			doc = self.load("B24 Access Grant", uid, h)
			if doc is None:
				continue
			doc.update(grant)
			doc.src_hash = h
			self.save(doc)
		self.mark_missing("B24 Access Grant", seen)
		self.counters["access_grants"] = len(seen)


def crm_resource(entity: str, smart: dict) -> tuple[str, str]:
	"""CRM permission entity code → (section, readable title)."""
	code, _, category = entity.partition("_C")
	if entity.startswith("DYNAMIC_"):
		rest = entity[len("DYNAMIC_") :]
		type_id, _, category = rest.partition("_C")
		title = smart.get(type_id) or f"Смарт-процесс {type_id}"
		return "Смарт-процесс", title + (f" (воронка {category})" if category else "")
	if code == "DEAL" and category:
		return "CRM", f"Сделки (воронка {category})"
	return "CRM", CRM_ENTITIES.get(entity, entity)


class PrincipalNames:
	"""Readable names of Bitrix24 access codes."""

	def __init__(self, portal, export):
		self.portal = portal
		self.users = dict(
			frappe.get_all(
				"B24 User",
				filters={"portal": portal},
				fields=["b24_id", "full_name"],
				as_list=True,
				limit_page_length=0,
			)
		)
		self.departments = dict(
			frappe.get_all(
				"B24 Department",
				filters={"portal": portal},
				fields=["b24_id", "department_name"],
				as_list=True,
				limit_page_length=0,
			)
		)
		self.groups = dict(
			frappe.get_all(
				"B24 Workgroup",
				filters={"portal": portal},
				fields=["b24_id", "group_name"],
				as_list=True,
				limit_page_length=0,
			)
		)
		self.user_groups = {str(g.get("id")): g.get("name") for g in export.get("user_groups") or []}

	def title(self, code: str) -> str:
		code = code or ""
		kind, number, suffix = parse_access_code(code)
		if kind == "U":
			return self.users.get(number) or f"пользователь {number}"
		if kind == "D":
			return f"Подразделение «{self.departments.get(number) or number}»"
		if kind == "DR":
			return f"Подразделение «{self.departments.get(number) or number}» с подотделами"
		if kind == "G":
			return f"Группа пользователей «{self.user_groups.get(number) or number}»"
		if kind == "SG":
			who = {"A": "владелец", "E": "модераторы", "K": "все участники"}.get(suffix, "участники")
			return f"Рабочая группа «{self.groups.get(number) or number}»: {who}"
		if kind in ("AU", "UA", "IU"):
			return "Все сотрудники"
		return code


def parse_access_code(code: str) -> tuple[str, str, str]:
	"""U12 → (U, 12, ''), DR5 → (DR, 5, ''), SG7_K → (SG, 7, K), AU → (AU, '', '')."""
	code = (code or "").strip()
	for prefix in ("SG", "DR", "IU", "AU", "UA", "U", "D", "G"):
		if code.startswith(prefix):
			rest = code[len(prefix) :]
			number, _, suffix = rest.partition("_")
			if prefix in ("AU", "UA", "IU") or number.isdigit():
				return prefix, number, suffix
	return "", "", ""


# --------------------------------------------------------------------------- write-back


class ProfileWriter:
	"""Middle name, birth date and employee UUID from the HR data → Bitrix24 profiles."""

	def __init__(self, portal, client, sync_log=None, commit=True):
		self.portal = portal
		self.client = client
		self.sync_log = sync_log
		self.commit = commit
		self.warnings: list[str] = []

	def plan(self) -> list[tuple]:
		"""[(B24 User row, {BITRIX_FIELD: (local field, old, new)})]."""
		overwrite = self.portal.write_mode == "Пустые и отличающиеся"
		uuid_field = (self.portal.person_uuid_field or "").strip()
		users = frappe.db.sql(
			"""select u.name, u.b24_id, u.person, u.second_name, u.birthday, u.uuid_in_portal,
				p.middle_name, p.birth_date
			from `tabB24 User` u join `tabPerson` p on p.name = u.person
			where u.portal = %s and u.active = 1 and u.missing_in_source = 0
				and ifnull(u.user_type, '') in ('', 'employee')""",
			self.portal.name,
			as_dict=True,
		)
		plan = []
		for u in users:
			changes = {}
			if self.portal.write_middle_name and u.middle_name:
				current = u.second_name or ""
				if not current or (overwrite and normalize_name(current) != normalize_name(u.middle_name)):
					changes["SECOND_NAME"] = ("second_name", current, u.middle_name)
			if self.portal.write_birthday and u.birth_date:
				if not u.birthday or (overwrite and getdate(u.birthday) != getdate(u.birth_date)):
					changes["PERSONAL_BIRTHDAY"] = (
						"birthday",
						str(u.birthday or ""),
						getdate(u.birth_date).isoformat(),
					)
			if uuid_field and (u.uuid_in_portal or "").lower() != u.person:
				changes[uuid_field] = ("uuid_in_portal", u.uuid_in_portal or "", u.person)
			if changes:
				plan.append((u, changes))
		return plan

	def run(self) -> dict:
		plan = self.plan()
		total = sum(len(c) for _u, c in plan)
		limit = cint(self.portal.max_writes)
		if limit and total > limit:
			self.warnings.append(
				f"Предупреждение: в Битрикс24 нужно записать {total} значений, это больше предела {limit} "
				f"(«Не больше изменений за загрузку» в настройках портала). Ничего не записано: проверьте привязку "
				f"сотрудников и поднимите предел"
			)
			return {"b24_writes_planned": total, "b24_writes": 0}
		if plan and self.client is None:
			self.warnings.append("Предупреждение: нет подключения к Битрикс24, запись пропущена")
			return {"b24_writes_planned": total, "b24_writes": 0}
		written = failed = 0
		for user, changes in plan:
			fields = {b24: new for b24, (_local, _old, new) in changes.items()}
			error = None
			try:
				self.client.update_user(user.b24_id, fields)
			except Exception as e:
				error = str(e)[:500]
			for b24, (local, old, new) in changes.items():
				frappe.get_doc(
					{
						"doctype": "B24 Write Log",
						"portal": self.portal.name,
						"b24_user": user.name,
						"person": user.person,
						"field": b24,
						"old_value": old,
						"new_value": new,
						"status": "Ошибка" if error else "Записано",
						"error": error,
						"sync_log": self.sync_log,
					}
				).insert(ignore_permissions=True)
				if not error:
					frappe.db.set_value("B24 User", user.name, local, new, update_modified=False)
			if error:
				failed += 1
			else:
				written += len(changes)
			if self.commit:
				frappe.db.commit()
		if failed:
			self.warnings.append(
				f"Предупреждение: в Битрикс24 не удалось записать профили {failed} пользователей, см. B24 Write Log"
			)
		return {"b24_writes_planned": total, "b24_writes": written, "b24_write_errors": failed}
