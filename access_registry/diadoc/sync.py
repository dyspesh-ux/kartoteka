"""Mirror of the manual Diadoc registry xlsx upload into the registry.

One upload runs inside one transaction (a savepoint): an error or a tripped guard rolls
everything back, only the Sync Log survives. Missing users are marked, never deleted, and
only within the organizations present in the file.
"""

import json
import traceback
from collections import Counter

import frappe
from frappe.utils import cint, now_datetime

from access_registry.access_catalog.importer import save_changed, src_hash
from access_registry.access_roles.engine import refresh_counters
from access_registry.diadoc.linking import DiadocResolver
from access_registry.diadoc.parser import ParseError, parse
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user
from access_registry.sync.normalize import normalize_name

SAVEPOINT = "diadoc_sync"
LOG_KIND = "Диадок"
DERIVED_VERSION = 1
# Unlike the global guard_min_records (20) a small Diadoc org of 3-5 people must still be
# protected against a truncated file.
GUARD_MIN_RECORDS = 3


class DiadocGuardTripped(frappe.ValidationError):
	pass


def org_key(title: str) -> str:
	"""Normalized organization title for matching: the export has no stable IDs."""
	value = str(title or "").lower().replace("ё", "е")
	for quote in "«»“”‘’":
		value = value.replace(quote, '"')
	return " ".join(value.split())


def user_key(org_name: str, login: str, full_name: str) -> str:
	if login:
		return f"{org_name}:{login}"
	return f"{org_name}:фио:{normalize_name(full_name)}"


def run_upload(data: bytes, commit: bool = True):
	"""Parses one xlsx export and mirrors it in one transaction. Returns the Sync Log."""
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
	status = "Ошибка"
	org_titles = []
	try:
		parsed = parse(data)
		importer = DiadocImport()
		stats = importer.run(parsed)
		messages += parsed["warnings"]
		messages += importer.warnings
		org_titles = importer.org_titles
		status = "Успех"
	except DiadocGuardTripped as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except ParseError as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		messages.append(f"Выгрузка не разобрана: {e}")
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		messages.append(traceback.format_exc())
	finally:
		_switch_user(previous_user, None)
	log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps(stats, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	log.save(ignore_permissions=True)
	# the card update goes by the registered organization name: the file title may be a
	# re-cased/quoted variant of an existing organization (org_key matched it)
	org_names = stats.get("org_names", {})
	for title in org_titles:
		org_name = org_names.get(title) or title
		if not frappe.db.exists("Diadoc Organization", org_name):
			continue
		values = {"last_status": f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})"}
		if status == "Успех":
			values["last_upload"] = log.started
			org_stats = stats.get("organizations", {}).get(title, {})
			values["users_count"] = org_stats.get("users", 0)
			values["linked_count"] = org_stats.get("linked", 0)
		frappe.db.set_value("Diadoc Organization", org_name, values, update_modified=False)
	if commit:
		frappe.db.commit()
	return log


class DiadocImport:
	def __init__(self):
		self.settings = get_settings()
		self.counters = Counter()
		self.warnings: list[str] = []
		self.resolver = DiadocResolver()
		self.org_titles: list[str] = []

	def warn(self, text):
		self.warnings.append(f"Предупреждение: {text}")

	def run(self, parsed: dict) -> dict:
		users = parsed["users"]
		self.org_titles = parsed["orgs"]
		org_names = self.ensure_organizations(parsed["orgs"])
		self.check_guard(users, org_names)
		seen = set()
		for row in users:
			org_name = org_names[org_key(row["org"])]
			uid = user_key(org_name, row["login"], row["full_name"])
			seen.add(uid)
			self.import_user(org_name, uid, row)
		self.mark_missing(org_names, seen)
		self.refresh_org_counters(org_names)
		refresh_counters()
		return {
			"org_names": {title: org_names[org_key(title)] for title in self.org_titles},
			"organizations": self.org_stats,
			"Diadoc User": {
				"created": self.counters["Diadoc User: создано"],
				"updated": self.counters["Diadoc User: изменено"],
				"unchanged": self.counters["Diadoc User: без изменений"],
				"missing": self.counters["Diadoc User: пропало"],
			},
			"warnings": self.counters["warnings"],
		}

	# ------------------------------------------------------------ organizations

	def ensure_organizations(self, titles: list) -> dict:
		"""Matches file titles to existing organizations by org_key, creates the missing ones."""
		existing = {}
		for name, title in frappe.get_all(
			"Diadoc Organization", fields=["name", "title"], as_list=True, limit_page_length=0
		):
			existing[org_key(title or name)] = name
		org_names = {}
		for title in titles:
			key = org_key(title)
			if key in existing:
				org_names[key] = existing[key]
				if existing[key] != title:
					self.warn(f"организация «{title}» уже есть в реестре как «{existing[key]}»")
			else:
				doc = frappe.get_doc(
					{"doctype": "Diadoc Organization", "name": title, "title": title}
				).insert(ignore_permissions=True)
				org_names[key] = doc.name
				self.counters["Diadoc Organization: создано"] += 1
		return org_names

	# ------------------------------------------------------------ guard

	def check_guard(self, users: list, org_names: dict):
		threshold = cint(self.settings.shrink_threshold_pct)
		received = Counter(org_names[org_key(row["org"])] for row in users)
		for org_name, count in received.items():
			actual = frappe.db.count("Diadoc User", {"organization": org_name, "missing_in_source": 0})
			self.counters[f"guard: {org_name}"] = f"в реестре {actual}, в выгрузке {count}"
			if actual < GUARD_MIN_RECORDS:
				continue
			if count < actual * (1 - threshold / 100):
				shrink = round(100 * (actual - count) / actual, 1)
				raise DiadocGuardTripped(
					f"Загрузка Диадок остановлена предохранителем: в выгрузке {count} пользователей "
					f"организации «{org_name}», актуальных в реестре {actual} — сокращение на {shrink}%, "
					f"допустимо не более {threshold}%. Изменения не записаны. Если сокращение ожидаемое, "
					f"временно поднимите «Допустимое сокращение выгрузки, %» в Access Registry Settings "
					f"до {int(shrink) + 1} или выше и повторите загрузку."
				)

	# ------------------------------------------------------------ users

	def load_user(self, uid, payload_hash):
		"""Returns the document to fill, or None when nothing changed."""
		if frappe.db.exists("Diadoc User", uid):
			current = frappe.db.get_value("Diadoc User", uid, ["src_hash", "missing_in_source"], as_dict=True)
			if current.src_hash == payload_hash and not current.missing_in_source:
				return None
			return frappe.get_doc("Diadoc User", uid)
		doc = frappe.new_doc("Diadoc User")
		doc.uid = uid
		return doc

	def import_user(self, org_name: str, uid: str, row: dict):
		existing = (
			frappe.db.get_value("Diadoc User", uid, ["manual_person", "person"], as_dict=True)
			if frappe.db.exists("Diadoc User", uid)
			else None
		)
		if existing and existing.manual_person:
			person, method, note = existing.manual_person, "Вручную", ""
		else:
			person, method, note = self.resolver.resolve(row["full_name"], row["login"])
		payload = {"u": row, "person": [person, method, note], "v": DERIVED_VERSION}
		h = src_hash(payload)
		doc = self.load_user(uid, h)
		if doc is None:
			self.counters["Diadoc User: без изменений"] += 1
			return
		doc.update(
			{
				"organization": org_name,
				"full_name": row["full_name"],
				"position": row["position"],
				"login": row["login"],
				"src_hash": h,
				"missing_in_source": 0,
			}
		)
		for field, value in row["rights"].items():
			doc.set(field, value)
		doc.person = person
		doc.person_link_method = method
		doc.person_link_note = note
		doc.flags.ignore_permissions = True
		is_new = doc.is_new()
		if save_changed(doc):
			self.counters[f"Diadoc User: {'создано' if is_new else 'изменено'}"] += 1
		else:
			self.counters["Diadoc User: без изменений"] += 1
		if doc.flags.cut_fields:
			self.warn(f"{doc.name}: слишком длинные значения обрезаны: {', '.join(doc.flags.cut_fields)}")

	# ------------------------------------------------------------ housekeeping

	def mark_missing(self, org_names: dict, seen: set):
		for name in frappe.get_all(
			"Diadoc User",
			filters={"organization": ["in", list(org_names.values())], "missing_in_source": 0},
			pluck="name",
			limit_page_length=0,
		):
			if name in seen:
				continue
			doc = frappe.get_doc("Diadoc User", name)
			doc.missing_in_source = 1
			doc.flags.ignore_permissions = True
			doc.save(ignore_version=False)
			self.counters["Diadoc User: пропало"] += 1

	def refresh_org_counters(self, org_names: dict):
		self.org_stats = {}
		for title in self.org_titles:
			name = org_names[org_key(title)]
			total = linked = 0
			for row in frappe.get_all(
				"Diadoc User",
				filters={"organization": name, "missing_in_source": 0},
				fields=["person"],
				limit_page_length=0,
			):
				total += 1
				linked += int(bool(row.person))
			self.org_stats[title] = {"users": total, "linked": linked}
