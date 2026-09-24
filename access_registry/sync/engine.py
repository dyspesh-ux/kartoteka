"""Synchronisation of one 1C:ZUP source (HR_Export_API) into the registry.

The whole sync of a source runs inside one transaction (a savepoint): an error or
a tripped guard rolls everything back, only the Sync Log survives.
"""

import json
import traceback
from collections import Counter, defaultdict

import frappe
from frappe.utils import add_days, cint, getdate, now_datetime

from access_registry.settings import get_settings
from access_registry.sync import client
from access_registry.sync.departments import ROOT_KEY, ensure_root, org_node_key
from access_registry.sync.normalize import comparable, date_or_none, match_keys
from access_registry.sync.persons import (
	ACTIVE_EMPLOYMENT_STATUSES,
	compute_person_state,
	create_merge_candidate,
)

LOCK_NAME = "access_registry:zup_sync"
SAVEPOINT = "access_registry_sync"
MAX_MESSAGES = 1000

REASON_FULL_MATCH = "Совпадают ФИО и дата рождения"
REASON_PARTIAL_MATCH = "Совпадают имя, отчество и дата рождения (смена фамилии?)"

ORG_FIELDS = [
	"source",
	"guid",
	"title",
	"full_title",
	"prefix",
	"inn",
	"kpp",
	"head_organization",
	"legal_entity",
	"missing",
]
DEPT_FIELDS = [
	"source",
	"guid",
	"title",
	"code",
	"node_type",
	"organization",
	"zup_parent_guid",
	"missing",
	"parent_hr_department",
	"zup_head",
	"manual_head",
	"head_candidate",
]
EMPLOYMENT_FIELDS = [
	"source",
	"guid",
	"person",
	"person_guid",
	"tab_number",
	"title",
	"organization",
	"department",
	"position",
	"hire_date",
	"termination_date",
	"employment_kind_code",
	"employment_kind",
	"zup_state",
	"status",
	"missing",
	"state_code",
	"state",
	"category",
	"absent_from",
	"absent_to",
	"absent_until_expected",
	"part_time_share",
	"actually_working",
]
ABSENCE_FIELDS = [
	"source",
	"employment_guid",
	"employment",
	"person",
	"state_code",
	"state",
	"category",
	"date_from",
	"date_to",
	"until_expected",
	"part_time_share",
	"actually_working",
	"org_source",
	"cancelled",
]
SUSPICIOUS_CATEGORIES = ("Неизвестно", "Отсутствует")


class GuardTripped(Exception):
	pass


# --------------------------------------------------------------------------- jobs


def enqueue_all_sources():
	"""Scheduler entry point: one job in the ``long`` queue per enabled source."""
	for name in frappe.get_all("HR Source", filters={"enabled": 1}, pluck="name"):
		enqueue_source_sync(name)


def scheduled_sync_day():
	enqueue_all_sources()


def scheduled_sync_night():
	enqueue_all_sources()


def enqueue_source_sync(source: str):
	settings = get_settings()
	return frappe.enqueue(
		"access_registry.sync.engine.run_source_sync",
		queue="long",
		timeout=settings.job_timeout,
		job_id=job_id_for(source),
		deduplicate=True,
		source=source,
	)


def job_id_for(source: str) -> str:
	return f"access_registry_sync::{source}"


def run_source_sync(source: str, today=None, commit: bool = True, fetch=None):
	"""Runs the synchronisation of one source and returns its Sync Log.

	``today`` and ``fetch`` exist for tests; ``commit=False`` leaves transaction
	control to the caller (the sync itself is still isolated by a savepoint).
	"""
	settings = get_settings()
	fetch = fetch or client.fetch_source_data
	log = frappe.get_doc(
		{
			"doctype": "Sync Log",
			"source": source,
			"kind": "ЗУП",
			"status": "В процессе",
			"started": now_datetime(),
		}
	).insert(ignore_permissions=True)
	if commit:
		frappe.db.commit()

	sync = None
	status = "Ошибка"
	messages = []
	meta_snapshot = None
	previous_user = frappe.session.user
	locked = _acquire_lock(settings.job_timeout)
	try:
		if not locked:
			raise frappe.ValidationError(
				"Не дождались окончания синхронизации другого источника (синки выполняются по очереди)."
			)
		_switch_user(settings.sync_user, messages)
		frappe.db.savepoint(SAVEPOINT)
		try:
			sync = SourceSync(source, today=today, sync_log=log.name)
			data = fetch(sync.source, sync.window_from, sync.window_to, settings.http_timeout)
			meta_snapshot = data.get("meta")
			sync.run(data)
			sync.mark_source_synced()
			status = "Успех"
		except GuardTripped as e:
			_rollback_sync()
			status = "Остановлен предохранителем"
			messages.append(str(e))
		except Exception:
			messages.append(traceback.format_exc())
			_rollback_sync()
			status = "Ошибка"
	except Exception:
		status = "Ошибка"
		messages.append(traceback.format_exc())
	finally:
		_switch_user(previous_user, None)
		if locked:
			_release_lock()

	if sync:
		messages = sync.warnings + messages
	log_exists = frappe.db.exists("Sync Log", log.name)
	if log_exists:
		log.reload()
	log.status = status
	log.finished = now_datetime()
	log.first_load = cint(sync.first_load) if sync else 0
	stats = sync.stats_dict() if sync else {}
	if status != "Успех":
		stats["_результат"] = "изменения откатены, счётчики показывают, что успел сделать синк до остановки"
	log.stats = json.dumps(stats, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	if meta_snapshot is not None:
		log.meta_snapshot = json.dumps(meta_snapshot, ensure_ascii=False, indent=1)
	if log_exists:
		log.save(ignore_permissions=True)
	else:
		log.db_insert()
	frappe.db.set_value("HR Source", source, "last_status", _status_line(status, log), update_modified=False)
	if commit:
		frappe.db.commit()
	return log


def _rollback_sync():
	"""Undoes everything the sync wrote. The Sync Log row was created before the savepoint."""
	try:
		frappe.db.rollback(save_point=SAVEPOINT)
	except Exception:
		# The server may have aborted the whole transaction (deadlock, lost connection):
		# the savepoint is gone, roll back completely. With commit=True the log is already committed.
		frappe.db.rollback()


def _status_line(status, log):
	return f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})"


def _format_messages(messages):
	if len(messages) > MAX_MESSAGES:
		extra = len(messages) - MAX_MESSAGES
		messages = messages[:MAX_MESSAGES] + [f"… и ещё {extra} сообщений"]
	return "\n".join(messages)


def _switch_user(user, messages):
	if not user:
		return
	if user != "Administrator" and user != "Guest" and not frappe.db.exists("User", user):
		if messages is not None:
			messages.append(
				f"Пользователь синка {user} не найден, изменения записаны от имени {frappe.session.user}"
			)
		return
	if frappe.session.user != user:
		frappe.set_user(user)


def _acquire_lock(timeout):
	return cint(frappe.db.sql("select get_lock(%s, %s)", (LOCK_NAME, cint(timeout)))[0][0]) == 1


def _release_lock():
	frappe.db.sql("select release_lock(%s)", (LOCK_NAME,))


# --------------------------------------------------------------------------- sync


def first(row: dict, *keys):
	for key in keys:
		value = row.get(key)
		if value not in (None, ""):
			return value
	return None


def clean(value) -> str:
	if value is None:
		return ""
	return str(value).strip()


class SourceSync:
	def __init__(self, source: str, today=None, sync_log: str | None = None):
		self.source = frappe.get_doc("HR Source", source)
		self.code = self.source.name
		self.settings = get_settings()
		self.today = getdate(today) if today else getdate()
		self.window_from = add_days(self.today, -cint(self.settings.absence_days_back))
		self.window_to = add_days(self.today, cint(self.settings.absence_days_ahead))
		self.first_load = not self.source.last_sync
		self.sync_log = sync_log
		self.stats = defaultdict(Counter)
		self.warnings: list[str] = []
		self._meta_cache = {}

	# ------------------------------------------------------------ utilities

	def key(self, guid) -> str:
		return f"{self.code}:{guid}"

	def warn(self, text: str):
		self.warnings.append(f"Предупреждение: {text}")
		self.stats["warnings"]["total"] += 1

	def stats_dict(self):
		return {entity: dict(counter) for entity, counter in self.stats.items()}

	def fieldtype(self, doctype, fieldname):
		meta = self._meta_cache.get(doctype)
		if not meta:
			meta = self._meta_cache[doctype] = frappe.get_meta(doctype)
		df = meta.get_field(fieldname)
		return df.fieldtype if df else "Data"

	def changed_values(self, doctype, current: dict, values: dict) -> dict:
		return {
			k: v
			for k, v in values.items()
			if comparable(current.get(k), self.fieldtype(doctype, k))
			!= comparable(v, self.fieldtype(doctype, k))
		}

	def load_existing(self, doctype, fields, filters) -> dict:
		rows = frappe.get_all(doctype, filters=filters, fields=["name", *fields], limit_page_length=0)
		return {r.name: r for r in rows}

	def upsert(self, doctype, name, values, current=None, entity=None):
		"""Creates or updates a record, saving only when something really changed.

		Returns (action, changed_fields). ``current`` is the preloaded row or None.
		"""
		entity = entity or doctype
		if current is None and frappe.db.exists(doctype, name):
			current = frappe.db.get_value(doctype, name, list(values), as_dict=True)
		if current is not None:
			changed = self.changed_values(doctype, current, values)
			if not changed:
				self.stats[entity]["unchanged"] += 1
				return "unchanged", {}
			doc = frappe.get_doc(doctype, name)
			doc.update(changed)
			doc.save(ignore_permissions=True, ignore_version=False)
			self.stats[entity]["updated"] += 1
			return "updated", changed
		doc = frappe.new_doc(doctype)
		doc.update(values)
		if doc.meta.get_field("record_key"):
			doc.record_key = name
		doc.insert(ignore_permissions=True)
		self.stats[entity]["created"] += 1
		return "created", values

	def add_event(self, event_type, person=None, employment=None, event_date=None, details=None):
		if self.first_load:
			self.stats["HR Event"]["suppressed_first_load"] += 1
			return
		frappe.get_doc(
			{
				"doctype": "HR Event",
				"event_type": event_type,
				"person": person,
				"employment": employment,
				"event_date": event_date or self.today,
				"details": details,
				"source": self.code,
				"sync_log": self.sync_log,
			}
		).insert(ignore_permissions=True)
		self.stats["HR Event"][event_type] += 1

	def mark_source_synced(self):
		frappe.db.set_value("HR Source", self.code, "last_sync", now_datetime(), update_modified=False)

	# ------------------------------------------------------------ orchestration

	def run(self, data: dict):
		self.check_meta(data)
		organizations = self.index_rows(
			data.get("organizations") or [], ("ОрганизацияGUID", "GUID"), "организаций"
		)
		departments = self.index_rows(
			data.get("departments") or [], ("ПодразделениеGUID", "GUID"), "подразделений"
		)
		employees = self.index_rows(data.get("employees") or [], ("СотрудникGUID",), "сотрудников")
		self.check_guard(employees, departments)

		ensure_root()
		self.sync_organizations(organizations)
		self.sync_departments(departments)
		self.sync_positions(employees)
		affected_persons = self.sync_employments(employees)
		self.update_persons(affected_persons)
		self.sync_heads(departments)
		self.sync_absences(data.get("absences") or [])

	def check_meta(self, data):
		if data.get("meta_error"):
			self.warn(f"/meta не получен: {data['meta_error']}")
		meta = data.get("meta")
		if meta is None:
			return
		suspicious = []

		def walk(node):
			if isinstance(node, dict):
				for key, value in node.items():
					if "Категория" in key and value in SUSPICIOUS_CATEGORIES:
						label = first(
							node, "Наименование", "Представление", "Состояние", "Код"
						) or json.dumps(node, ensure_ascii=False)
						suspicious.append(f"{label} → {value}")
					walk(value)
			elif isinstance(node, list):
				for item in node:
					walk(item)

		walk(meta)
		for item in sorted(set(suspicious)):
			self.warn(f"вид состояния с категорией «Неизвестно/Отсутствует» в /meta: {item}")

	def index_rows(self, rows, key_names, label) -> dict:
		result = {}
		for row in rows:
			guid = first(row, *key_names)
			if not guid:
				self.warn(
					f"в выгрузке {label} запись без GUID пропущена: {json.dumps(row, ensure_ascii=False)[:300]}"
				)
				continue
			if guid in result:
				self.warn(f"в выгрузке {label} GUID {guid} встречается несколько раз, взята последняя запись")
			result[guid] = row
		return result

	# ------------------------------------------------------------ guard

	def check_guard(self, employees: dict, departments: dict):
		threshold = cint(self.settings.shrink_threshold_pct)
		minimum = cint(self.settings.guard_min_records)
		checks = [
			("сотрудников", "Employment", {"source": self.code, "missing": 0}, len(employees)),
			(
				"подразделений",
				"HR Department",
				{"source": self.code, "missing": 0, "node_type": "Подразделение"},
				len(departments),
			),
		]
		for label, doctype, filters, received in checks:
			actual = frappe.db.count(doctype, filters)
			self.stats["guard"][f"{doctype}: в реестре"] = actual
			self.stats["guard"][f"{doctype}: в выгрузке"] = received
			if actual < minimum:
				continue
			if received < actual * (1 - threshold / 100):
				shrink = round(100 * (actual - received) / actual, 1)
				raise GuardTripped(
					f"Синк остановлен предохранителем: в выгрузке {received} {label}, "
					f"а актуальных в реестре {actual} — сокращение на {shrink}%, "
					f"допустимо не более {threshold}%. Изменения не записаны.\n"
					f"Если сокращение ожидаемое (массовое увольнение, чистка справочника), временно поднимите "
					f"«Допустимое сокращение выгрузки, %» в Access Registry Settings до {int(shrink) + 1} или выше, "
					f"запустите синк источника {self.code} кнопкой «Синхронизировать сейчас» и верните прежнее значение."
				)

	# ------------------------------------------------------------ organizations

	def ensure_legal_entity(self, inn, kpp, title, full_title):
		name = f"{inn}-{kpp}" if kpp else inn
		if not frappe.db.exists("Legal Entity", name):
			frappe.get_doc(
				{
					"doctype": "Legal Entity",
					"inn": inn,
					"kpp": kpp,
					"title": title,
					"full_title": full_title,
				}
			).insert(ignore_permissions=True)
			self.stats["Legal Entity"]["created"] += 1
		return name

	def sync_organizations(self, organizations: dict):
		existing = self.load_existing("HR Organization", ORG_FIELDS, {"source": self.code})
		prepared = {}
		for guid, row in organizations.items():
			title = clean(row.get("Наименование"))
			full_title = clean(row.get("НаименованиеПолное"))
			inn = clean(row.get("ИНН"))
			kpp = clean(row.get("КПП"))
			legal_entity = ""
			if inn:
				legal_entity = self.ensure_legal_entity(inn, kpp, title, full_title)
			else:
				self.warn(f"у организации «{title}» ({guid}) нет ИНН — юрлицо не назначено")
			head_guid = clean(row.get("ГоловнаяОрганизацияGUID"))
			if head_guid and head_guid != guid and head_guid not in organizations:
				self.warn(f"головная организация {head_guid} для «{title}» отсутствует в выгрузке")
				head_guid = ""
			prepared[guid] = (
				{
					"source": self.code,
					"guid": guid,
					"title": title,
					"full_title": full_title,
					"prefix": clean(row.get("Префикс")),
					"inn": inn,
					"kpp": kpp,
					"legal_entity": legal_entity,
					"missing": 0,
				},
				head_guid if head_guid != guid else "",
			)

		# Head organisations first so that the link can be validated.
		done = set()
		pending = list(prepared)
		while pending:
			progress = False
			for guid in list(pending):
				values, head_guid = prepared[guid]
				if head_guid and head_guid not in done:
					continue
				values["head_organization"] = self.key(head_guid) if head_guid else ""
				self.upsert("HR Organization", self.key(guid), values, existing.get(self.key(guid)))
				done.add(guid)
				pending.remove(guid)
				progress = True
			if not progress:
				for guid in pending:
					prepared[guid][0]["head_organization"] = ""
					self.warn(f"цикл головных организаций, связь не проставлена: {guid}")
					self.upsert(
						"HR Organization", self.key(guid), prepared[guid][0], existing.get(self.key(guid))
					)
				break

		for name, row in existing.items():
			if row.guid not in organizations and not row.missing:
				self.upsert("HR Organization", name, {"missing": 1}, row)
				self.stats["HR Organization"]["missing"] += 1

		# One tree node per organisation under the root.
		nodes = self.load_existing(
			"HR Department", DEPT_FIELDS, {"source": self.code, "node_type": "Организация"}
		)
		all_orgs = {row.guid: row for row in existing.values()}
		for guid in organizations:
			all_orgs[guid] = frappe._dict(guid=guid, title=prepared[guid][0]["title"], missing=0)
		for guid, org in all_orgs.items():
			values = {
				"source": self.code,
				"guid": guid,
				"title": org.title,
				"node_type": "Организация",
				"organization": self.key(guid),
				"missing": 1 if guid not in organizations else 0,
			}
			current = nodes.get(org_node_key(self.code, guid))
			if current is None:
				values["parent_hr_department"] = ROOT_KEY
			self.upsert(
				"HR Department",
				org_node_key(self.code, guid),
				values,
				current,
				entity="HR Department (организации)",
			)
		self.org_guids = set(all_orgs)

	# ------------------------------------------------------------ departments

	def sync_departments(self, departments: dict):
		existing = self.load_existing(
			"HR Department", DEPT_FIELDS, {"source": self.code, "node_type": "Подразделение"}
		)
		org_nodes = {}
		for guid, row in departments.items():
			org_guid = clean(row.get("ОрганизацияGUID"))
			if org_guid and org_guid in self.org_guids:
				org_nodes[guid] = org_node_key(self.code, org_guid)
			else:
				org_nodes[guid] = ROOT_KEY
				self.warn(
					f"подразделение «{clean(row.get('Наименование'))}» ({guid}): организация {org_guid or '—'} "
					f"не найдена, узел повешен на корень"
				)

		# Desired parents; processing by depth guarantees a parent is handled before its children,
		# so a move never creates a loop.
		desired = {}
		for guid, row in departments.items():
			parent_guid = clean(row.get("РодительGUID"))
			if parent_guid and parent_guid != guid and parent_guid in departments:
				desired[guid] = parent_guid
			else:
				if parent_guid and parent_guid != guid:
					self.warn(
						f"подразделение «{clean(row.get('Наименование'))}» ({guid}): родитель {parent_guid} "
						f"не найден в выгрузке, узел повешен на организацию"
					)
				desired[guid] = None

		depth, cycles = resolve_hierarchy(desired)
		for cycle in cycles:
			self.warn(f"цикл в иерархии подразделений: {', '.join(cycle)} — узлы повешены на организацию")

		# Pass 1: upsert attributes. New nodes are inserted straight under their final parent
		# (already created, it is shallower) or under their organisation.
		order = sorted(departments, key=lambda g: depth.get(g, 0))
		for guid in order:
			row = departments[guid]
			org_guid = clean(row.get("ОрганизацияGUID"))
			values = {
				"source": self.code,
				"guid": guid,
				"title": clean(row.get("Наименование")),
				"code": clean(row.get("Код")),
				"node_type": "Подразделение",
				"organization": self.key(org_guid) if org_guid in self.org_guids else "",
				"missing": 0,
			}
			name = self.key(guid)
			current = existing.get(name)
			if current is None:
				values["parent_hr_department"] = self.key(desired[guid]) if desired[guid] else org_nodes[guid]
				values["zup_parent_guid"] = clean(row.get("РодительGUID"))
			self.upsert("HR Department", name, values, current)

		# Pass 2: move existing nodes whose parent changed; the parent GUID is saved with the move
		# (one version per node).
		current = {
			r.name: r
			for r in frappe.get_all(
				"HR Department",
				filters={"source": self.code, "node_type": "Подразделение"},
				fields=["name", "parent_hr_department", "zup_parent_guid"],
				limit_page_length=0,
			)
		}
		for guid in order:
			name = self.key(guid)
			target = self.key(desired[guid]) if desired[guid] else org_nodes[guid]
			zup_parent_guid = clean(departments[guid].get("РодительGUID"))
			node = current[name]
			move = (node.parent_hr_department or "") != target
			if move or (node.zup_parent_guid or "") != zup_parent_guid:
				doc = frappe.get_doc("HR Department", name)
				doc.parent_hr_department = target
				doc.zup_parent_guid = zup_parent_guid
				doc.save(ignore_permissions=True, ignore_version=False)
				if move:
					self.stats["HR Department"]["moved"] += 1
				else:
					self.stats["HR Department"]["updated"] += 1

		for name, row in existing.items():
			if row.guid not in departments and not row.missing:
				self.upsert("HR Department", name, {"missing": 1}, row)
				self.stats["HR Department"]["missing"] += 1
		self.department_guids = set(departments) | {row.guid for row in existing.values()}

	# ------------------------------------------------------------ positions

	def sync_positions(self, employees: dict):
		existing = self.load_existing("HR Position", ["source", "guid", "title"], {"source": self.code})
		positions = {}
		for row in employees.values():
			guid = clean(row.get("ДолжностьGUID"))
			if guid:
				positions[guid] = clean(row.get("Должность"))
		for guid, title in positions.items():
			name = self.key(guid)
			self.upsert(
				"HR Position", name, {"source": self.code, "guid": guid, "title": title}, existing.get(name)
			)
		self.position_guids = set(positions) | {row.guid for row in existing.values()}

	# ------------------------------------------------------------ persons & employments

	def load_source_ids(self):
		rows = frappe.get_all(
			"Person Source ID",
			filters={"source": self.code, "parenttype": "Person"},
			fields=["name", "parent", "person_guid", "last_name", "first_name", "middle_name", "birth_date"],
			limit_page_length=0,
		)
		self.source_ids = {row.person_guid: row for row in rows}

	def resolve_person(self, person_guid, last_name, first_name, middle_name, birth_date) -> str:
		incoming = {
			"last_name": last_name,
			"first_name": first_name,
			"middle_name": middle_name,
			"birth_date": birth_date,
		}
		link = self.source_ids.get(person_guid)
		if link:
			if self.changed_values("Person Source ID", link, incoming):
				person = frappe.get_doc("Person", link.parent)
				for row in person.source_ids:
					if row.source == self.code and row.person_guid == person_guid:
						row.update(incoming)
				# The person's own name follows the latest change reported by any source.
				person.update(incoming)
				person.save(ignore_permissions=True, ignore_version=False)
				link.update(incoming)
				self.stats["Person"]["updated"] += 1
			else:
				self.stats["Person"]["unchanged"] += 1
			return link.parent

		key, partial_key = match_keys(last_name, first_name, middle_name, birth_date)
		full_matches = []
		if key:
			full_matches = frappe.get_all("Person", filters={"match_key": key}, pluck="name")
			if len(full_matches) == 1:
				has_own = frappe.db.exists(
					"Person Source ID",
					{"parent": full_matches[0], "parenttype": "Person", "source": self.code},
				)
				if not has_own:
					person = frappe.get_doc("Person", full_matches[0])
					row = person.append(
						"source_ids", {"source": self.code, "person_guid": person_guid, **incoming}
					)
					person.save(ignore_permissions=True, ignore_version=False)
					self.source_ids[person_guid] = frappe._dict(row.as_dict())
					self.source_ids[person_guid].parent = person.name
					self.stats["Person"]["linked_by_match_key"] += 1
					return person.name

		person = frappe.get_doc(
			{
				"doctype": "Person",
				**incoming,
				"source_ids": [{"source": self.code, "person_guid": person_guid, **incoming}],
			}
		).insert(ignore_permissions=True)
		self.source_ids[person_guid] = frappe._dict(
			parent=person.name, person_guid=person_guid, source=self.code, **incoming
		)
		self.stats["Person"]["created"] += 1

		for other in full_matches:
			if create_merge_candidate(other, person.name, REASON_FULL_MATCH):
				self.stats["Person Merge Candidate"]["created"] += 1
		if partial_key:
			partial = frappe.get_all(
				"Person",
				filters={
					"match_key_partial": partial_key,
					"match_key": ["!=", key],
					"name": ["!=", person.name],
				},
				pluck="name",
			)
			for other in partial:
				if create_merge_candidate(other, person.name, REASON_PARTIAL_MATCH):
					self.stats["Person Merge Candidate"]["created"] += 1
		return person.name

	def employment_status(self, hire_date, termination_date) -> str:
		if not hire_date or hire_date > self.today:
			return "Не принят"
		if termination_date and termination_date < self.today:
			return "Уволен"
		if termination_date:
			return "Увольняется"
		return "Работает"

	def sync_employments(self, employees: dict) -> set:
		self.load_source_ids()
		existing = self.load_existing("Employment", EMPLOYMENT_FIELDS, {"source": self.code})
		affected_persons = {row.person for row in existing.values() if row.person}

		by_person = defaultdict(list)
		for guid, row in employees.items():
			person_guid = clean(row.get("ФизЛицоGUID"))
			if not person_guid:
				self.warn(f"сотрудник {guid} ({clean(row.get('ТабельныйНомер'))}) без ФизЛицоGUID пропущен")
				continue
			by_person[person_guid].append((guid, row))

		seen = set()
		for person_guid, items in by_person.items():
			ref = items[0][1]
			names = (
				clean(ref.get("Фамилия")),
				clean(ref.get("Имя")),
				clean(ref.get("Отчество")),
				date_or_none(ref.get("ДатаРождения")),
			)
			for _guid, row in items[1:]:
				other = (
					clean(row.get("Фамилия")),
					clean(row.get("Имя")),
					clean(row.get("Отчество")),
					date_or_none(row.get("ДатаРождения")),
				)
				if other != names:
					self.warn(
						f"у физлица {person_guid} разные ФИО/дата рождения в записях сотрудников, взята первая"
					)
			person = self.resolve_person(person_guid, *names)
			affected_persons.add(person)
			full_name = " ".join(filter(None, names[:3]))
			for guid, row in items:
				seen.add(guid)
				self.sync_employment(guid, row, person, person_guid, full_name, existing.get(self.key(guid)))

		for name, row in existing.items():
			if row.guid in seen or row.missing:
				continue
			self.upsert("Employment", name, {"missing": 1, "status": "Нет в выгрузке"}, row)
			self.stats["Employment"]["missing"] += 1
			self.add_event("Пропал из выгрузки", row.person, name, details=f"Трудоустройство {row.title}")
		return affected_persons

	def sync_employment(self, guid, row, person, person_guid, full_name, current):
		name = self.key(guid)
		hire_date = date_or_none(row.get("ДатаПриема"))
		termination_date = date_or_none(row.get("ДатаУвольнения"))
		org_guid = clean(row.get("ОрганизацияGUID"))
		dept_guid = clean(row.get("ПодразделениеGUID"))
		position_guid = clean(row.get("ДолжностьGUID"))
		if org_guid and org_guid not in self.org_guids:
			self.warn(f"сотрудник {guid}: организация {org_guid} не найдена")
		if dept_guid and dept_guid not in self.department_guids:
			self.warn(f"сотрудник {guid}: подразделение {dept_guid} не найдено")
		position_title = clean(row.get("Должность"))
		values = {
			"source": self.code,
			"guid": guid,
			"person": person,
			"person_guid": person_guid,
			"tab_number": clean(row.get("ТабельныйНомер")),
			"title": " · ".join(filter(None, [full_name, position_title])),
			"organization": self.key(org_guid) if org_guid in self.org_guids else "",
			"department": self.key(dept_guid) if dept_guid in self.department_guids else "",
			"position": self.key(position_guid) if position_guid else "",
			"hire_date": hire_date,
			"termination_date": termination_date,
			"employment_kind_code": clean(row.get("ВидЗанятостиКод")),
			"employment_kind": clean(row.get("ВидЗанятости")),
			"zup_state": clean(row.get("Состояние")),
			"status": self.employment_status(hire_date, termination_date),
			"missing": 0,
			"state_code": clean(row.get("КадровоеСостояниеКод")),
			"state": clean(row.get("КадровоеСостояние")),
			"category": clean(row.get("Категория")),
			"absent_from": date_or_none(row.get("ОтсутствуетС")),
			"absent_to": date_or_none(row.get("ОтсутствуетПо")),
			"absent_until_expected": date_or_none(row.get("ОкончаниеПредположительно")),
			"part_time_share": row.get("ДоляНеполногоВремени") or 0,
			"actually_working": 1 if row.get("ФактическиРаботает") else 0,
		}
		self.upsert("Employment", name, values, current)
		if current is None:
			return

		if current.missing:
			self.add_event("Вернулся в выгрузку", person, name, details=f"Трудоустройство {values['title']}")
		if termination_date and not current.termination_date and termination_date >= self.today:
			self.add_event(
				"Предстоящее увольнение",
				person,
				name,
				event_date=termination_date,
				details=f"Последний рабочий день {termination_date.isoformat()}",
			)
		if values["status"] in ACTIVE_EMPLOYMENT_STATUSES:
			changes = []
			if current.department and values["department"] and current.department != values["department"]:
				changes.append(
					"Подразделение: "
					f"{self.title_of('HR Department', current.department)} → "
					f"{self.title_of('HR Department', values['department'])}"
				)
			if current.position and values["position"] and current.position != values["position"]:
				changes.append(
					"Должность: "
					f"{self.title_of('HR Position', current.position)} → "
					f"{self.title_of('HR Position', values['position'])}"
				)
			if changes:
				self.add_event("Перевод", person, name, details="\n".join(changes))

	def title_of(self, doctype, name):
		return frappe.db.get_value(doctype, name, "title") or name

	# ------------------------------------------------------------ person statuses

	def update_persons(self, persons: set):
		for person in sorted(p for p in persons if p):
			if not frappe.db.exists("Person", person):
				continue
			employments = frappe.get_all(
				"Employment",
				filters={"person": person},
				fields=[
					"name",
					"status",
					"employment_kind_code",
					"category",
					"actually_working",
					"hire_date",
					"termination_date",
				],
				limit_page_length=0,
			)
			new = compute_person_state(employments)
			current = frappe.db.get_value(
				"Person", person, ["status", "presence", "external_part_time_only"], as_dict=True
			)
			changed = self.changed_values("Person", current, new)
			if not changed:
				continue
			doc = frappe.get_doc("Person", person)
			doc.update(changed)
			doc.save(ignore_permissions=True, ignore_version=False)
			self.stats["Person"]["status_updated"] += 1
			self.person_events(person, current, new, employments)

	def person_events(self, person, old, new, employments):
		active = [e for e in employments if e.status in ACTIVE_EMPLOYMENT_STATUSES]
		if new["status"] == "Работает" and old.status != "Работает":
			hires = [e for e in active if e.hire_date]
			latest = max(hires, key=lambda e: e.hire_date) if hires else None
			self.add_event(
				"Приём",
				person,
				latest.name if latest else None,
				event_date=latest.hire_date if latest else None,
			)
		elif old.status == "Работает" and new["status"] == "Уволен":
			ended = [e for e in employments if e.termination_date]
			latest = max(ended, key=lambda e: e.termination_date) if ended else None
			self.add_event(
				"Увольнение",
				person,
				latest.name if latest else None,
				event_date=latest.termination_date if latest else None,
			)
		if new["status"] == "Работает" and old.presence != new["presence"]:
			leave = [e for e in active if e.category == "ОтпускПоУходу"]
			employment = leave[0].name if leave else None
			if old.presence == "На месте" and new["presence"] == "Длительное отсутствие":
				self.add_event("Уход в отпуск по уходу", person, employment)
			elif old.presence == "Длительное отсутствие" and new["presence"] == "На месте":
				self.add_event("Выход из отпуска по уходу", person, employment)

	# ------------------------------------------------------------ department heads

	def sync_heads(self, departments: dict):
		keywords = [k.lower().replace("ё", "е") for k in self.settings.head_keywords_list]
		depts = frappe.get_all(
			"HR Department",
			filters={"source": self.code, "node_type": "Подразделение", "missing": 0},
			fields=["name", "guid", "title", "zup_head", "manual_head", "head_candidate"],
			limit_page_length=0,
		)
		active = frappe.get_all(
			"Employment",
			filters={"source": self.code, "status": ["in", ACTIVE_EMPLOYMENT_STATUSES]},
			fields=["name", "department", "person", "position", "employment_kind_code", "hire_date"],
			limit_page_length=0,
		)
		positions = dict(
			frappe.get_all(
				"HR Position",
				filters={"source": self.code},
				fields=["name", "title"],
				as_list=True,
				limit_page_length=0,
			)
		)
		by_dept = defaultdict(list)
		for emp in active:
			by_dept[emp.department].append(emp)

		for dept in depts:
			row = departments.get(dept.guid) or {}
			head_guid = clean(row.get("РуководительФизЛицоGUID"))
			zup_head = ""
			if head_guid:
				link = self.source_ids.get(head_guid)
				if link:
					zup_head = link.parent
				else:
					self.warn(
						f"руководитель подразделения «{dept.title}» (физлицо {head_guid}) не найден среди сотрудников"
					)
			candidate = ""
			if not zup_head and not dept.manual_head:
				candidate = self.pick_head_candidate(by_dept.get(dept.name, []), positions, keywords)
			if (dept.zup_head or "") != zup_head or (dept.head_candidate or "") != candidate:
				doc = frappe.get_doc("HR Department", dept.name)
				doc.zup_head = zup_head
				doc.head_candidate = candidate
				doc.save(ignore_permissions=True, ignore_version=False)
				self.stats["HR Department"]["head_updated"] += 1

	@staticmethod
	def pick_head_candidate(employments, positions, keywords) -> str:
		for keyword in keywords:
			matches = [
				e
				for e in employments
				if (positions.get(e.position) or "").lower().replace("ё", "е").strip().startswith(keyword)
			]
			if matches:
				matches.sort(
					key=lambda e: (
						0 if e.employment_kind_code == "ОсновноеМестоРаботы" else 1,
						str(e.hire_date or "9999-12-31"),
						e.name,
					)
				)
				return matches[0].person
		return ""

	# ------------------------------------------------------------ absences

	def sync_absences(self, rows: list):
		window_from, window_to = getdate(self.window_from), getdate(self.window_to)
		starts = [date_or_none(r.get("ДатаНачала")) for r in rows]
		lowest = min([d for d in starts if d] + [window_from])
		existing = self.load_existing(
			"HR Absence",
			ABSENCE_FIELDS + ["date_from"],
			{"source": self.code, "date_from": [">=", lowest]},
		)
		employments = dict(
			frappe.get_all(
				"Employment",
				filters={"source": self.code},
				fields=["guid", "person"],
				as_list=True,
				limit_page_length=0,
			)
		)
		seen = set()
		unknown = Counter()
		for row in rows:
			emp_guid = clean(row.get("СотрудникGUID"))
			code = clean(row.get("Код"))
			date_from = date_or_none(row.get("ДатаНачала"))
			if not emp_guid or not code or not date_from:
				self.warn(
					f"отсутствие без СотрудникGUID/Код/ДатаНачала пропущено: {json.dumps(row, ensure_ascii=False)[:300]}"
				)
				continue
			name = f"{self.code}:{emp_guid}:{code}:{date_from.isoformat()}"
			if name in seen:
				self.warn(f"отсутствие {name} встречается в выгрузке несколько раз")
			seen.add(name)
			person = employments.get(emp_guid)
			if person is None:
				unknown[emp_guid] += 1
				link = self.source_ids.get(clean(row.get("ФизЛицоGUID")))
				person = link.parent if link else ""
			values = {
				"source": self.code,
				"employment_guid": emp_guid,
				"employment": self.key(emp_guid) if emp_guid in employments else "",
				"person": person or "",
				"state_code": code,
				"state": clean(first(row, "Состояние", "КадровоеСостояние")),
				"category": clean(row.get("Категория")),
				"date_from": date_from,
				"date_to": date_or_none(row.get("ДатаОкончания")),
				"until_expected": date_or_none(row.get("ОкончаниеПредположительно")),
				"part_time_share": row.get("ДоляНеполногоВремени") or 0,
				"actually_working": 1 if row.get("ФактическиРаботает") else 0,
				"org_source": clean(row.get("ИсточникОрганизации")),
				"cancelled": 0,
			}
			self.upsert("HR Absence", name, values, existing.get(name))
		for emp_guid, count in unknown.items():
			self.warn(f"отсутствия ({count}) сотрудника {emp_guid}, которого нет среди трудоустройств")

		for name, row in existing.items():
			if name in seen or row.cancelled:
				continue
			if row.date_from and window_from <= getdate(row.date_from) <= window_to:
				self.upsert("HR Absence", name, {"cancelled": 1}, row)
				self.stats["HR Absence"]["cancelled"] += 1


def resolve_hierarchy(desired: dict) -> tuple[dict, list]:
	"""Depth of every node in the desired forest; cycles are broken (nodes become roots).

	``desired`` maps node → parent node or None and is modified in place for cycles.
	"""
	depth = {}
	cycles = []
	for start in desired:
		path = []
		on_path = set()
		node = start
		while node is not None and node not in depth and node not in on_path:
			path.append(node)
			on_path.add(node)
			node = desired.get(node)
		if node is not None and node in on_path:
			idx = path.index(node)
			cycle = path[idx:]
			cycles.append(cycle)
			for item in cycle:
				desired[item] = None
				depth[item] = 1
			path = path[:idx]
			base = 1
		else:
			base = depth[node] if node is not None else 0
		for item in reversed(path):
			base += 1
			depth[item] = base
	return depth, cycles
