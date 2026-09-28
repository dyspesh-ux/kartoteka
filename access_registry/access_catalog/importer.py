"""Import of the 1C:ZUP rights snapshot (ITAccess /snapshot) and event log (/log).

Only reads the source: nothing is ever written back to 1C.
"""

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_system_timezone, now_datetime

from access_registry.settings import get_settings

ORGS_KIND = "Организации"
ORGS_TEXT = {
	"all": "все",
	"not_configured": "не настроено",
}
EVENT_TITLES = {
	"_$User$_.New": "Пользователь ИБ создан",
	"_$User$_.Update": "Пользователь ИБ изменён",
	"_$User$_.Delete": "Пользователь ИБ удалён",
	"_$Data$_.New": "Данные добавлены",
	"_$Data$_.Update": "Данные изменены",
	"_$Data$_.Delete": "Данные удалены",
}
LOG_CURSOR_OVERLAP = timedelta(minutes=1)
LOG_CURSOR_DEFAULT = timedelta(days=7)


class CatalogGuardTripped(frappe.ValidationError):
	pass


# --------------------------------------------------------------------------- helpers


def resolve_base_code(base_code: str) -> str:
	"""The base code is the code of an existing HR Source; the case does not matter."""
	code = (base_code or "").strip()
	if not code:
		frappe.throw(_("Не указан base_code"))
	if frappe.db.exists("HR Source", code):
		return frappe.db.get_value("HR Source", code, "name")
	for name in frappe.get_all("HR Source", pluck="name"):
		if name.lower() == code.lower():
			return name
	frappe.throw(_("Нет источника HR Source с кодом {0}").format(code), frappe.DoesNotExistError)


def parse_json_arg(value) -> dict:
	if isinstance(value, str | bytes):
		value = json.loads(value)
	if not isinstance(value, dict):
		frappe.throw(_("Ожидался JSON-объект"))
	return value


def src_hash(payload) -> str:
	return hashlib.md5(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def orgs_text(restrictions: list) -> tuple[str, str]:
	"""Human text and mode of the «Организации» restriction of one profile."""
	for restriction in restrictions or []:
		if restriction.get("kind") != ORGS_KIND:
			continue
		mode = restriction.get("mode") or ""
		names = ", ".join(v.get("name") or "" for v in restriction.get("values") or [])
		if mode == "only":
			return names or "ни одной", mode
		if mode == "all_except":
			return f"все, кроме: {names}", mode
		return ORGS_TEXT.get(mode, mode), mode
	return "все (без ограничения)", "unrestricted"


def canonical_rights(rights: dict | None) -> dict | None:
	"""Order-independent form of user_rights: 1C may return rows in any order."""
	if not rights:
		return None
	profiles = []
	for profile in rights.get("profiles") or []:
		restrictions = []
		for r in profile.get("restrictions") or []:
			restrictions.append(
				{
					**r,
					"values": sorted(
						r.get("values") or [], key=lambda v: (v.get("id") or "", v.get("name") or "")
					),
				}
			)
		restrictions.sort(key=lambda r: (r.get("kind") or "", r.get("source") or ""))
		profiles.append({**profile, "restrictions": restrictions})
	profiles.sort(key=lambda p: (p.get("profile_id") or "", p.get("access_group_id") or ""))
	orgs = rights.get("organizations") or {}
	return {
		**rights,
		"profiles": profiles,
		"organizations": {"all": bool(orgs.get("all")), "list": sorted(orgs.get("list") or [])},
	}


def canonical_ib(ib: dict | None) -> dict | None:
	if not ib:
		return None
	return {**ib, "roles": sorted(set(ib.get("roles") or []))}


def to_site_datetime(value) -> datetime | None:
	"""ISO 8601 with offset (as 1C writes it) → naive datetime in the site time zone."""
	if not value:
		return None
	dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	if dt.tzinfo is not None:
		dt = dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return dt


def to_iso_with_offset(value: datetime) -> str:
	return value.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat(timespec="seconds")


def sync_child_table(doc, fieldname: str, key_fields: tuple, rows: list[dict]) -> None:
	"""Updates a child table in place so that Version shows only rows really added/removed/changed."""
	existing = {tuple(row.get(k) or "" for k in key_fields): row for row in doc.get(fieldname)}
	result = []
	for values in rows:
		key = tuple(values.get(k) or "" for k in key_fields)
		row = existing.pop(key, None)
		if row is None:
			row = doc.append(fieldname, {})
		for field, value in values.items():
			if row.get(field) != value:
				row.set(field, value)
		result.append(row)
	for idx, row in enumerate(result, start=1):
		row.idx = idx
	doc.set(fieldname, result)


# --------------------------------------------------------------------------- snapshot


class SnapshotImport:
	def __init__(self, base_code: str):
		self.base = resolve_base_code(base_code)
		self.settings = get_settings()
		self.counters = Counter()
		self.warnings: list[str] = []

	def warn(self, text: str):
		self.warnings.append(f"Предупреждение: {text}")

	def run(self, data: dict) -> dict:
		profiles = [p for p in data.get("profiles") or [] if p.get("id")]
		users = [u for u in data.get("users") or [] if u.get("id")]
		orphans = [o for o in data.get("ib_orphans") or [] if o.get("ib_id")]
		self.check_guard(len(users) + len(orphans))

		profile_roles = self.import_profiles(profiles)
		rights = {}
		for item in data.get("user_rights") or []:
			if item.get("user_id") in rights:
				self.warn(f"права пользователя {item.get('user_id')} встречаются в снимке несколько раз")
			rights[item.get("user_id")] = item
		self.person_by_guid = dict(
			frappe.get_all(
				"Person Source ID",
				filters={"source": self.base, "parenttype": "Person"},
				fields=["person_guid", "parent"],
				as_list=True,
			)
		)
		seen = set()
		for user in users:
			uid = f"{self.base}:{user['id']}"
			seen.add(uid)
			self.import_user(uid, user, rights.get(user["id"]), profile_roles, orphan=False)
		for ib in orphans:
			uid = f"{self.base}:ib:{ib['ib_id']}"
			seen.add(uid)
			user = {"name": ib.get("full_name") or ib.get("login"), "ib": ib}
			self.import_user(uid, user, None, profile_roles, orphan=True)
		self.counters["missing"] += self.mark_missing("ZUP User", seen)
		self.update_folders(profiles, rights)

		self.counters["profiles"] = len(profiles)
		self.counters["users"] = len(users)
		self.counters["orphans"] = len(orphans)
		return {
			"profiles": self.counters["profiles"],
			"users": self.counters["users"],
			"orphans": self.counters["orphans"],
			"changed": self.counters["changed"],
			"missing": self.counters["missing"],
		}

	def check_guard(self, received: int):
		threshold = cint(self.settings.shrink_threshold_pct)
		actual = frappe.db.count("ZUP User", {"base_code": self.base, "missing_in_source": 0})
		if actual < cint(self.settings.guard_min_records):
			return
		if received < actual * (1 - threshold / 100):
			shrink = round(100 * (actual - received) / actual, 1)
			raise CatalogGuardTripped(
				f"Снимок прав базы {self.base} отклонён предохранителем: пользователей в снимке {received}, "
				f"актуальных в каталоге {actual} — сокращение на {shrink}%, допустимо не более {threshold}%. "
				f"Изменения не записаны. Если сокращение ожидаемое, временно поднимите «Допустимое сокращение "
				f"выгрузки, %» в Access Registry Settings до {int(shrink) + 1} или выше и повторите загрузку."
			)

	def save(self, doc):
		doc.flags.ignore_permissions = True
		if doc.is_new():
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True, ignore_version=False)
		self.counters["changed"] += 1

	def load(self, doctype, uid, payload_hash):
		"""Returns the document to fill, or None when nothing changed."""
		if frappe.db.exists(doctype, uid):
			current = frappe.db.get_value(doctype, uid, ["src_hash", "missing_in_source"], as_dict=True)
			if current.src_hash == payload_hash and not current.missing_in_source:
				return None
			return frappe.get_doc(doctype, uid)
		doc = frappe.new_doc(doctype)
		doc.uid = uid
		return doc

	def import_profiles(self, profiles: list) -> dict:
		profile_roles = {}
		seen = set()
		for p in profiles:
			uid = f"{self.base}:{p['id']}"
			seen.add(uid)
			roles = sorted(
				{(r.get("name") or "", r.get("title") or "") for r in p.get("roles") or []},
			)
			profile_roles[p["id"]] = {name for name, _title in roles}
			payload = {**p, "roles": [{"name": n, "title": t} for n, t in roles]}
			h = src_hash(payload)
			doc = self.load("ZUP Access Profile", uid, h)
			if doc is None:
				continue
			doc.base_code = self.base
			doc.profile_id = p["id"]
			doc.profile_name = p.get("name") or p["id"]
			doc.supplied = cint(p.get("supplied"))
			doc.deleted = cint(p.get("deleted"))
			sync_child_table(
				doc, "roles", ("role_name",), [{"role_name": n, "role_title": t} for n, t in roles]
			)
			doc.src_hash = h
			doc.missing_in_source = 0
			self.save(doc)
		self.counters["missing"] += self.mark_missing("ZUP Access Profile", seen)
		return profile_roles

	def import_user(self, uid, user, rights, profile_roles, orphan):
		ib = canonical_ib(user.get("ib"))
		rights = canonical_rights(rights)
		expected = set()
		for p in (rights or {}).get("profiles") or []:
			expected |= profile_roles.get(p.get("profile_id"), set())
		person = self.person_by_guid.get(user.get("person_id")) if user.get("person_id") else None
		payload = {
			"u": {**user, "ib": ib},
			"r": rights,
			# Derived from other records: a change there must also refresh this user.
			"expected_roles": sorted(expected),
			"person": person,
			"orphan": orphan,
		}
		h = src_hash(payload)
		doc = self.load("ZUP User", uid, h)
		if doc is None:
			return
		ib = ib or {}
		doc.base_code = self.base
		doc.user_id = None if orphan else user.get("id")
		doc.user_name = user.get("name") or ib.get("full_name") or ib.get("login") or uid
		doc.person_id = user.get("person_id")
		doc.person_name = user.get("person_name")
		doc.person = person
		doc.department_name = user.get("department_name")
		doc.invalid = cint(user.get("invalid"))
		doc.service = cint(user.get("service"))
		doc.deleted = cint(user.get("deleted"))
		doc.is_orphan = cint(orphan)
		doc.ib_id = ib.get("ib_id")
		doc.login = ib.get("login")
		doc.ad_domain = ib.get("ad_domain")
		doc.ad_login = ib.get("ad_login")
		doc.login_allowed = cint(ib.get("login_allowed"))
		doc.auth_standard = cint(ib.get("auth_standard"))
		doc.auth_os = cint(ib.get("auth_os"))
		doc.auth_openid = cint(ib.get("auth_openid"))

		profile_rows = []
		for p in (rights or {}).get("profiles") or []:
			text, mode = orgs_text(p.get("restrictions"))
			profile_uid = f"{self.base}:{p.get('profile_id')}"
			if not frappe.db.exists("ZUP Access Profile", profile_uid):
				self.warn(
					f"профиль {p.get('profile_id')} ({p.get('profile_name')}) не найден среди профилей снимка"
				)
				profile_uid = None
			profile_rows.append(
				{
					"profile": profile_uid,
					"profile_name": p.get("profile_name"),
					"access_group_id": p.get("access_group_id"),
					"access_group_name": p.get("access_group_name"),
					"direct": cint(p.get("direct")),
					"via_name": p.get("via_name"),
					"orgs_mode": mode,
					"orgs_text": text,
					"restrictions_json": json.dumps(
						p.get("restrictions") or [], ensure_ascii=False, indent=1
					),
				}
			)
		sync_child_table(doc, "profiles", ("profile", "access_group_id"), profile_rows)

		if rights:
			orgs = rights.get("organizations") or {}
			doc.all_orgs = cint(orgs.get("all"))
			doc.orgs_text = "все" if orgs.get("all") else ", ".join(orgs.get("list") or [])
		else:
			doc.all_orgs = 0
			doc.orgs_text = ""

		roles = ib.get("roles") or []
		sync_child_table(
			doc,
			"ib_roles",
			("role_name",),
			[{"role_name": r, "in_profiles": int(r in expected)} for r in roles],
		)
		extra = sorted(set(roles) - expected)
		doc.extra_roles = "\n".join(extra)
		doc.has_extra_roles = int(bool(extra))
		doc.src_hash = h
		doc.missing_in_source = 0
		self.save(doc)

	def mark_missing(self, doctype, seen) -> int:
		count = 0
		for name in frappe.get_all(
			doctype, filters={"base_code": self.base, "missing_in_source": 0}, pluck="name"
		):
			if name in seen:
				continue
			doc = frappe.get_doc(doctype, name)
			doc.missing_in_source = 1
			doc.save(ignore_permissions=True, ignore_version=False)
			count += 1
		return count

	def update_folders(self, profiles, rights):
		referenced = {p.get("profile_id") for r in rights.values() for p in r.get("profiles") or []}
		for p in profiles:
			uid = f"{self.base}:{p['id']}"
			is_folder = int(not p.get("roles") and p["id"] not in referenced)
			if cint(frappe.db.get_value("ZUP Access Profile", uid, "is_folder")) != is_folder:
				doc = frappe.get_doc("ZUP Access Profile", uid)
				doc.is_folder = is_folder
				doc.save(ignore_permissions=True, ignore_version=False)


def import_snapshot_data(base_code: str, data: dict) -> tuple[dict, list[str]]:
	importer = SnapshotImport(base_code)
	result = importer.run(data)
	return result, importer.warnings


# --------------------------------------------------------------------------- event log


def event_uid(base: str, event: dict) -> str:
	parts = [base] + [
		str(event.get(k) or "") for k in ("date", "who", "event", "object_type", "object", "host")
	]
	return hashlib.md5("\x1f".join(parts).encode("utf-8")).hexdigest()


def import_log_data(base_code: str, payload: dict) -> dict:
	base = resolve_base_code(base_code)
	users_by_login = dict(
		frappe.get_all(
			"ZUP User",
			filters={"base_code": base, "login": ["is", "set"]},
			fields=["login", "name"],
			as_list=True,
		)
	)
	inserted = skipped = 0
	for event in payload.get("events") or []:
		uid = event_uid(base, event)
		if frappe.db.exists("ZUP Audit Event", uid):
			skipped += 1
			continue
		code = event.get("event") or ""
		frappe.get_doc(
			{
				"doctype": "ZUP Audit Event",
				"uid": uid,
				"base_code": base,
				"event_date": to_site_datetime(event.get("date")),
				"who": event.get("who"),
				"who_user": users_by_login.get(event.get("who")),
				"event": code,
				"event_title": EVENT_TITLES.get(code, code),
				"object_type": event.get("object_type"),
				"object": event.get("object"),
				"comment": event.get("comment"),
				"host": event.get("host"),
			}
		).insert(ignore_permissions=True)
		inserted += 1
	return {"inserted": inserted, "skipped": skipped}


def log_cursor(base_code: str) -> dict:
	base = resolve_base_code(base_code)
	latest = frappe.db.get_value("ZUP Audit Event", {"base_code": base}, "max(event_date)")
	if latest:
		start = get_datetime(latest) - LOG_CURSOR_OVERLAP
	else:
		start = now_datetime() - LOG_CURSOR_DEFAULT
	return {"from": to_iso_with_offset(start)}
