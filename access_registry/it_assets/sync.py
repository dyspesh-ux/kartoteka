"""Mirror of Snipe-IT: users, hardware and the activity log (checkouts, checkins, audits).

The registry only reads Snipe-IT (REST API, ``client.py``). Snipe-IT users are linked to employees
(employee number, AD login, e-mail, full name), and every asset handed out to a user gets the
employee, so the registry shows equipment in the employee's card and alerts on equipment of people
who no longer work.
"""

import hashlib
import html
import json
import re
import traceback
from collections import Counter, defaultdict

import frappe
from frappe.utils import add_days, cint, flt, now_datetime

from access_registry.access_catalog.importer import save_changed
from access_registry.access_catalog.linking import NameMatcher
from access_registry.it_assets.client import SnipeITClient, SnipeITError, parse_datetime
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user
from access_registry.sync.normalize import normalize_name

SAVEPOINT = "snipeit_sync"
LOG_KIND = "Snipe-IT"
DERIVED_VERSION = 1

BY_NUMBER = "Табельный номер"
BY_AD = "Учётка AD"
BY_MAIL = "Почта"
BY_NAME = "ФИО"
BY_HAND = "Вручную"

ACTIONS = {
	"checkout": "Выдача",
	"checkin from": "Возврат",
	"audit": "Аудит",
	"update": "Изменение",
	"create": "Создание",
	"delete": "Удаление",
	"restore": "Восстановление",
	"requested": "Запрос",
	"request canceled": "Запрос отменён",
	"accepted": "Принято",
	"declined": "Отклонено",
	"uploaded": "Файл",
	"note added": "Заметка",
}


STATUS_TYPES = ("deployable", "pending", "archived", "undeployable")


class SnipeITGuardTripped(frappe.ValidationError):
	pass


# --------------------------------------------------------------------------- jobs


def scheduled_snipeit_sync():
	for server in frappe.get_all("Snipe-IT Server", filters={"enabled": 1}, pluck="name"):
		enqueue(server)


def enqueue(server: str):
	return frappe.enqueue(
		"access_registry.it_assets.sync.run_server_sync",
		queue="long",
		timeout=get_settings().job_timeout,
		job_id=f"snipeit_sync::{server}",
		deduplicate=True,
		server=server,
	)


@frappe.whitelist(methods=["POST"])
def sync_now(server: str):
	frappe.only_for(("System Manager", "Registry Admin"))
	enqueue(server)
	return "queued"


@frappe.whitelist(methods=["POST"])
def test_connection(server: str) -> dict:
	frappe.only_for(("System Manager", "Registry Admin"))
	client = make_client(frappe.get_doc("Snipe-IT Server", server))
	hardware = client.get("hardware", {"limit": 1})
	users = client.get("users", {"limit": 1})
	return {"hardware": cint(hardware.get("total")), "users": cint(users.get("total"))}


def make_client(server) -> SnipeITClient:
	return SnipeITClient(
		server.base_url,
		server.get_password("api_token"),
		timeout=cint(get_settings().http_timeout) or 60,
		verify_ssl=bool(server.verify_ssl),
	)


def fetch_server(server, client) -> dict:
	"""Everything the mirror needs, as plain dicts (the shape of the tests' fixture)."""
	since = add_days(now_datetime(), -(cint(server.activity_days) or 180))
	data = {"users": client.users(), "hardware": client.hardware()}
	try:
		data["activity"] = client.activity(since)
	except SnipeITError as e:
		if e.status != 403:
			raise
		# the activity log is a report in Snipe-IT: without «Reports: view» the rest still loads
		data["activity"] = None
		data["activity_error"] = str(e)
	return data


def run_server_sync(server: str, commit: bool = True, fetch=None):
	"""Reads Snipe-IT and mirrors it in one transaction. ``fetch(server_doc) -> dict`` replaces the
	network in tests. Returns the Sync Log."""
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
	doc = frappe.get_doc("Snipe-IT Server", server)
	status = "Ошибка"
	try:
		data = fetch(doc) if fetch else fetch_server(doc, make_client(doc))
		importer = ServerImport(doc)
		stats = importer.run(data)
		messages += importer.warnings
		frappe.db.set_value("Snipe-IT Server", server, "last_sync", now_datetime(), update_modified=False)
		status = "Успех"
	except SnipeITGuardTripped as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except Exception:
		messages.append(traceback.format_exc())
		frappe.db.rollback(save_point=SAVEPOINT)
	_switch_user(previous_user, None)
	log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps({"server": server, **stats}, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	log.save(ignore_permissions=True)
	frappe.db.set_value(
		"Snipe-IT Server",
		server,
		"last_status",
		f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})",
		update_modified=False,
	)
	if commit:
		frappe.db.commit()
	return log


# --------------------------------------------------------------------------- helpers


def src_hash(payload) -> str:
	return hashlib.md5(
		json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
	).hexdigest()


def text(value) -> str:
	"""Snipe-IT escapes HTML in API strings («&quot;», «&amp;»)."""
	return html.unescape(str(value)).strip() if value not in (None, False) else ""


def name_of(value) -> str:
	return text(value.get("name")) if isinstance(value, dict) else text(value)


def date_of(value, key="date"):
	"""{"date": "2024-01-10", "formatted": "…"} or a plain string → date or None."""
	if isinstance(value, dict):
		value = value.get(key) or value.get("date") or value.get("datetime")
	dt = parse_datetime(value)
	return dt.date() if dt else None


def datetime_of(value):
	raw = (value.get("datetime") or value.get("date")) if isinstance(value, dict) else value
	return parse_datetime(raw)


def money(value) -> float:
	"""«1,234.50», «1 234,50», 1234.5 → 1234.5."""
	if isinstance(value, int | float):
		return float(value)
	raw = re.sub(r"[^\d,.\-]", "", text(value))
	if "," in raw and "." in raw:
		raw = raw.replace(",", "")
	return flt(raw.replace(",", "."))


def assigned_user_id(asset: dict):
	target = asset.get("assigned_to")
	if not isinstance(target, dict) or not target.get("id"):
		return None, None
	kind = text(target.get("type") or asset.get("assigned_type") or "user").lower()
	kind = (
		"user"
		if "user" in kind
		else "location"
		if "location" in kind
		else "asset"
		if "asset" in kind
		else kind
	)
	return kind, target


class PersonResolver:
	"""Snipe-IT user → employee. Order: manual (controller), employee number, AD login, e-mail,
	full name (or surname and first name when unique)."""

	def __init__(self):
		self.persons = {
			p.name: p
			for p in frappe.get_all("Person", fields=["name", "status", "name_key"], limit_page_length=0)
		}
		by_number = defaultdict(set)
		for e in frappe.get_all(
			"Employment",
			filters={"tab_number": ["is", "set"]},
			fields=["person", "tab_number"],
			limit_page_length=0,
		):
			by_number[e.tab_number.strip().lower()].add(e.person)
		self.by_number = {k: next(iter(v)) for k, v in by_number.items() if len(v) == 1}
		self.by_login, self.by_mail = {}, {}
		logins = Counter()
		for a in frappe.get_all(
			"AD Account",
			filters={"missing_in_source": 0},
			fields=["name", "person", "mail", "user_principal_name", "sam_account_name"],
			limit_page_length=0,
		):
			for mail in (a.mail, a.user_principal_name):
				if mail:
					self.by_mail[mail.lower()] = (a.person, a.name)
			if a.sam_account_name:
				logins[a.sam_account_name.lower()] += 1
				self.by_login[a.sam_account_name.lower()] = (a.person, a.name)
		for login, count in logins.items():
			if count > 1:  # the same login in several domains: ambiguous
				self.by_login.pop(login, None)
		for u in frappe.get_all(
			"B24 User",
			filters={"missing_in_source": 0, "person": ["is", "set"], "email": ["is", "set"]},
			fields=["person", "email"],
			limit_page_length=0,
		):
			self.by_mail.setdefault(u.email.lower(), (u.person, None))
		self.names = NameMatcher()
		self.short = defaultdict(list)
		for p in self.persons.values():
			parts = (p.name_key or "").split(" ")
			if len(parts) >= 2:
				self.short[" ".join(parts[:2])].append(p)

	def resolve(self, user: dict) -> tuple:
		"""Returns (person, method, note, ad_account)."""
		number = text(user.get("employee_num")).lower()
		login = text(user.get("username")).lower()
		if "\\" in login:
			login = login.split("\\", 1)[1]
		email = text(user.get("email")).lower()
		ad = self.by_login.get(login.split("@", 1)[0]) if login else None
		if not ad and "@" in login:
			ad = self.by_mail.get(login)
		ad_account = ad[1] if ad else None
		if number and number in self.persons:
			return number, BY_NUMBER, "", ad_account  # the registry's own employee id
		if number and number in self.by_number:
			return self.by_number[number], BY_NUMBER, "", ad_account
		if ad and ad[0]:
			return ad[0], BY_AD, "", ad_account
		if email and self.by_mail.get(email, (None,))[0]:
			person, account = self.by_mail[email]
			return person, BY_MAIL, "", ad_account or account
		last, first = text(user.get("last_name")), text(user.get("first_name"))
		full = " ".join(x for x in (last, first) if x) or text(user.get("name"))
		if len(full.split()) >= 3:
			person, note = self.names.match(full)
			return person, BY_NAME if person else "", note, ad_account
		key = normalize_name(full)
		candidates = self.short.get(key, []) if len(key.split(" ")) == 2 else []
		working = [c for c in candidates if c.status == "Работает"]
		if len(candidates) == 1:
			return candidates[0].name, BY_NAME, "", ad_account
		if len(working) == 1:
			return working[0].name, BY_NAME, "", ad_account
		if candidates:
			return None, "", f"сотрудников «{full}» несколько ({len(candidates)})", ad_account
		return None, "", f"сотрудник «{full}» не найден", ad_account


# --------------------------------------------------------------------------- import


class ServerImport:
	def __init__(self, server):
		self.server = server
		self.code = server.name
		self.settings = get_settings()
		self.counters = Counter()
		self.warnings: list[str] = []

	def warn(self, message):
		self.warnings.append(f"Предупреждение: {message}")

	def key(self, snipe_id) -> str:
		return f"{self.code}:{snipe_id}"

	def run(self, data: dict) -> dict:
		users = [u for u in data.get("users") or [] if u.get("id")]
		hardware = [a for a in data.get("hardware") or [] if a.get("id")]
		self.check_guard(len(hardware))
		self.import_users(users)
		self.import_hardware(hardware)
		if data.get("activity") is None and "activity" in data:
			self.warn(
				"журнал выдач и возвратов не загружен: "
				+ (data.get("activity_error") or "нет доступа")
				+ ". Техника и пользователи загружены. Дайте пользователю ключа право «Отчёты: просмотр» "
				"(Reports: View) в Snipe-IT"
			)
		else:
			self.import_activity(data.get("activity") or [])
		self.counters["users"] = len(users)
		self.counters["hardware"] = len(hardware)
		return dict(self.counters)

	def check_guard(self, received: int):
		threshold = cint(self.settings.shrink_threshold_pct)
		actual = frappe.db.count("IT Asset", {"server": self.code, "missing_in_source": 0})
		if actual < cint(self.settings.guard_min_records):
			return
		if received < actual * (1 - threshold / 100):
			shrink = round(100 * (actual - received) / actual, 1)
			raise SnipeITGuardTripped(
				f"Загрузка Snipe-IT {self.code} остановлена предохранителем: техники в Snipe-IT {received}, "
				f"в реестре {actual} — сокращение на {shrink}%, допустимо не более {threshold}%. Проверьте права "
				f"пользователя ключа API (видит ли он все компании). Если сокращение ожидаемое, временно поднимите "
				f"«Допустимое сокращение выгрузки, %» до {int(shrink) + 1} или выше."
			)

	def load(self, doctype, uid, payload_hash):
		if frappe.db.exists(doctype, uid):
			current = frappe.db.get_value(doctype, uid, ["src_hash", "missing_in_source"], as_dict=True)
			if current.src_hash == payload_hash and not current.missing_in_source:
				return None
			return frappe.get_doc(doctype, uid)
		doc = frappe.new_doc(doctype)
		doc.uid = uid
		return doc

	def save(self, doc):
		new = doc.is_new()
		doc.server = self.code
		doc.missing_in_source = 0
		if save_changed(doc):
			self.counters[f"{doc.doctype}: {'создано' if new else 'изменено'}"] += 1
		if doc.flags.cut_fields:
			self.warn(f"{doc.name}: слишком длинные значения обрезаны: {', '.join(doc.flags.cut_fields)}")

	def mark_missing(self, doctype, seen):
		count = 0
		for name in frappe.get_all(
			doctype, filters={"server": self.code, "missing_in_source": 0}, pluck="name"
		):
			if name not in seen:
				doc = frappe.get_doc(doctype, name)
				doc.missing_in_source = 1
				doc.save(ignore_permissions=True, ignore_version=False)
				count += 1
		if count:
			self.counters[f"{doctype}: пропало"] += count

	# ------------------------------------------------------------ users

	def import_users(self, users):
		resolver = PersonResolver()
		self.user_person = {}
		seen = set()
		for u in users:
			uid = self.key(u["id"])
			seen.add(uid)
			person, method, note, ad_account = resolver.resolve(u)
			payload = {"u": u, "p": [person, method, note, ad_account], "v": DERIVED_VERSION}
			h = src_hash(payload)
			doc = self.load("Snipe-IT User", uid, h)
			if doc is not None:
				doc.snipe_id = cint(u["id"])
				doc.full_name = text(u.get("name")) or " ".join(
					x for x in (text(u.get("last_name")), text(u.get("first_name"))) if x
				)
				doc.username = text(u.get("username"))
				doc.email = text(u.get("email"))
				doc.employee_num = text(u.get("employee_num"))
				doc.activated = cint(u.get("activated"))
				doc.department = name_of(u.get("department"))
				doc.location = name_of(u.get("location"))
				doc.assets_count = cint(u.get("assets_count"))
				doc.ldap_import = cint(u.get("ldap_import"))
				doc.ad_account = ad_account
				if not doc.manual_person:
					doc.person, doc.person_link_method, doc.person_link_note = person, method, note
				doc.src_hash = h
				self.save(doc)
			self.user_person[uid] = frappe.db.get_value("Snipe-IT User", uid, "person")
			if not self.user_person[uid]:
				self.counters["пользователи без сотрудника"] += 1
		self.mark_missing("Snipe-IT User", seen)

	# ------------------------------------------------------------ hardware

	def import_hardware(self, hardware):
		seen = set()
		for a in hardware:
			uid = self.key(a["id"])
			seen.add(uid)
			kind, target = assigned_user_id(a)
			user_uid = self.key(target["id"]) if kind == "user" else None
			if user_uid and not frappe.db.exists("Snipe-IT User", user_uid):
				self.warn(
					f"техника {uid} выдана пользователю {target.get('id')}, которого нет в выгрузке пользователей"
				)
				user_uid = None
			person = self.user_person.get(user_uid) if user_uid else None
			payload = {"a": a, "person": person, "v": DERIVED_VERSION}
			h = src_hash(payload)
			doc = self.load("IT Asset", uid, h)
			if doc is None:
				continue
			status = a.get("status_label") or {}
			doc.snipe_id = cint(a["id"])
			doc.asset_tag = text(a.get("asset_tag"))
			doc.asset_name = text(a.get("name")) or name_of(a.get("model")) or doc.asset_tag or uid
			doc.serial = text(a.get("serial"))
			doc.category = name_of(a.get("category"))
			doc.model = name_of(a.get("model"))
			doc.manufacturer = name_of(a.get("manufacturer"))
			doc.status_label = name_of(status)
			status_type = text(status.get("status_type")).lower() if isinstance(status, dict) else ""
			doc.status_type = status_type if status_type in STATUS_TYPES else ""
			doc.location = name_of(a.get("location")) or name_of(a.get("rtd_location"))
			doc.company = name_of(a.get("company"))
			doc.assigned_type = kind or ""
			doc.assigned_user = user_uid
			doc.assigned_name = name_of(target) if target else ""
			doc.person = person
			doc.last_checkout = datetime_of(a.get("last_checkout"))
			doc.expected_checkin = date_of(a.get("expected_checkin"))
			doc.last_audit_date = datetime_of(a.get("last_audit_date"))
			doc.next_audit_date = date_of(a.get("next_audit_date"))
			doc.purchase_date = date_of(a.get("purchase_date"))
			doc.purchase_cost = money(a.get("purchase_cost")) if a.get("purchase_cost") else 0
			doc.order_number = text(a.get("order_number"))
			doc.warranty_expires = date_of(a.get("warranty_expires"))
			doc.eol = date_of(a.get("asset_eol_date") or a.get("eol_date"))
			custom = a.get("custom_fields") or {}
			doc.custom_fields = "\n".join(
				f"{text(label)}: {text(field.get('value'))}"
				for label, field in sorted(custom.items())
				if isinstance(field, dict) and text(field.get("value"))
			)
			doc.notes = text(a.get("notes"))
			doc.updated_in_source = datetime_of(a.get("updated_at"))
			doc.src_hash = h
			self.save(doc)
		self.mark_missing("IT Asset", seen)

	# ------------------------------------------------------------ activity

	def import_activity(self, rows):
		days = cint(self.server.activity_days) or 180
		since = add_days(now_datetime(), -days)
		added = 0
		for row in rows:
			if not row.get("id"):
				continue
			uid = self.key(row["id"])
			if frappe.db.exists("IT Asset Event", uid):
				continue
			when = datetime_of(row.get("action_date") or row.get("created_at"))
			if when and when < since:
				continue
			item, target = row.get("item") or {}, row.get("target") or {}
			item_type = text(item.get("type")).lower()
			asset = self.key(item["id"]) if "asset" in item_type and item.get("id") else None
			target_type = text(target.get("type")).lower()
			target_user = self.key(target["id"]) if "user" in target_type and target.get("id") else None
			if target_user and not frappe.db.exists("Snipe-IT User", target_user):
				target_user = None
			action_type = text(row.get("action_type")).lower()
			frappe.get_doc(
				{
					"doctype": "IT Asset Event",
					"uid": uid,
					"server": self.code,
					"snipe_id": cint(row["id"]),
					"event_date": when,
					"action_type": action_type,
					"action": ACTIONS.get(action_type, action_type),
					"asset": asset if asset and frappe.db.exists("IT Asset", asset) else None,
					"item_name": name_of(item),
					"item_type": item_type,
					"target_name": name_of(target),
					"target_user": target_user,
					"person": self.user_person.get(target_user) if target_user else None,
					"admin_name": name_of(row.get("admin")),
					"note": text(row.get("note")),
				}
			).insert(ignore_permissions=True)
			added += 1
		frappe.db.delete("IT Asset Event", {"server": self.code, "event_date": ["<", since]})
		self.counters["журнал: новых записей"] = added
