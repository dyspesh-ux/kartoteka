"""Sync of a ZKBio CVAccess server (СКУД): personnel mirror and access events.

Read-only: nothing is written to the ACS database. Every run writes a Sync Log
(kind «СКУД») and runs inside one savepoint: an error or a tripped guard rolls
everything back, only the Sync Log survives.
"""

import hashlib
import json
import traceback
from collections import Counter, defaultdict
from datetime import timedelta

import frappe
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime

from access_registry.access_catalog.importer import save_changed
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user
from access_registry.sync.normalize import date_or_none, normalize_name

SAVEPOINT = "zkbio_sync"
DERIVED_VERSION = 1

BY_NAME = "ФИО"
BY_MANUAL = "Вручную"


class ZKBioGuardTripped(frappe.ValidationError):
	pass


def pass_nos(server) -> set[int]:
	"""Event codes treated as passages; empty config means the default {0}."""
	raw = server.get("pass_event_nos") or ""
	codes = {cint(part) for part in str(raw).split(",") if part.strip().isdigit()}
	return codes or {0}


def src_hash(payload) -> str:
	return hashlib.md5(
		json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
	).hexdigest()


def clean(value) -> str:
	if value is None:
		return ""
	return str(value).strip()


# --------------------------------------------------------------------------- jobs


def scheduled_persons_sync():
	for server in frappe.get_all("ZKBio Server", filters={"enabled": 1}, pluck="name"):
		enqueue(server, "persons")


def scheduled_events_sync():
	for server in frappe.get_all("ZKBio Server", filters={"enabled": 1}, pluck="name"):
		enqueue(server, "events")


def enqueue(server: str, phase: str):
	return frappe.enqueue(
		"access_registry.zkbio.sync.run_server_sync",
		queue="long",
		timeout=get_settings().job_timeout,
		job_id=job_id_for(server, phase),
		deduplicate=True,
		server=server,
		phase=phase,
	)


def job_id_for(server: str, phase: str) -> str:
	return f"zkbio_sync::{phase}::{server}"


def _rollback():
	"""Undoes everything the sync wrote. The Sync Log row was created before the savepoint."""
	try:
		frappe.db.rollback(save_point=SAVEPOINT)
	except Exception:
		# The server may have aborted the whole transaction (deadlock, lost connection):
		# the savepoint is gone, roll back completely. With commit=True the log is already committed.
		frappe.db.rollback()


def run_server_sync(server: str, phase: str, commit: bool = True, fetch=None):
	"""Runs one phase («persons» or «events») of a server's sync. Returns the Sync Log."""
	from access_registry.zkbio import pg_client

	settings = get_settings()
	log = frappe.get_doc(
		{"doctype": "Sync Log", "kind": "СКУД", "status": "В процессе", "started": now_datetime()}
	).insert(ignore_permissions=True)
	if commit:
		frappe.db.commit()
	messages = []
	sync = None
	previous_user = frappe.session.user
	_switch_user(settings.sync_user, messages)
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc = frappe.get_doc("ZKBio Server", server)
		sync = ServerSync(doc, sync_log=log.name)
		if phase == "persons":
			rows = (fetch or pg_client.fetch_persons)(doc)
			sync.sync_persons(rows)
		elif phase == "events":
			sync.run_events(fetch or pg_client.fetch_events)
		else:
			frappe.throw(f"Неизвестная фаза синка СКУД: {phase}")
		frappe.db.set_value("ZKBio Server", server, "last_sync", now_datetime(), update_modified=False)
		status = "Успех"
	except ZKBioGuardTripped as e:
		_rollback()
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except Exception:
		messages.append(traceback.format_exc())
		_rollback()
		status = "Ошибка"
	finally:
		_switch_user(previous_user, None)
	if sync:
		messages = sync.warnings + messages
	log_exists = frappe.db.exists("Sync Log", log.name)
	if log_exists:
		log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps(
		{"server": server, "phase": phase, **(sync.stats_dict() if sync else {})},
		ensure_ascii=False,
		indent=1,
		sort_keys=True,
	)
	log.messages = _format_messages(messages)
	if log_exists:
		log.save(ignore_permissions=True)
	else:
		log.db_insert()
	frappe.db.set_value(
		"ZKBio Server",
		server,
		"last_status",
		f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})",
		update_modified=False,
	)
	if commit:
		frappe.db.commit()
	return log


# --------------------------------------------------------------------------- linking


class PersonMatcher:
	"""Finds a Person by «last first» (ZKBio has no middle name). Same tie-break as NameMatcher."""

	def __init__(self):
		self.by_key = defaultdict(list)
		for row in frappe.get_all(
			"Person", fields=["name", "last_name", "first_name", "status"], limit_page_length=0
		):
			key = normalize_name(f"{row.last_name or ''} {row.first_name or ''}").strip()
			if key:
				self.by_key[key].append(row)

	def match(self, full_name: str) -> tuple[str | None, str]:
		"""Returns (person, note); note explains why nobody was found."""
		key = normalize_name(full_name)
		if not key:
			return None, "ФИО не указано"
		candidates = self.by_key.get(key, [])
		if len(candidates) == 1:
			return candidates[0].name, ""
		if len(candidates) > 1:
			working = [c for c in candidates if c.status == "Работает"]
			if len(working) == 1:
				return working[0].name, ""
			return None, f"несколько сотрудников с ФИО «{full_name}» ({len(candidates)})"
		return None, f"сотрудник с ФИО «{full_name}» не найден"


# --------------------------------------------------------------------------- sync


class ServerSync:
	def __init__(self, server, sync_log: str | None = None):
		self.server = server
		self.code = server.name
		self.settings = get_settings()
		self.sync_log = sync_log
		self.stats = defaultdict(Counter)
		self.warnings: list[str] = []

	def key(self, zkbio_id) -> str:
		return f"{self.code}:{zkbio_id}"

	def warn(self, text: str):
		self.warnings.append(f"Предупреждение: {text}")
		self.stats["warnings"]["total"] += 1

	def stats_dict(self):
		return {entity: dict(counter) for entity, counter in self.stats.items()}

	def check_guard(self, received: int):
		threshold = cint(self.settings.shrink_threshold_pct)
		actual = frappe.db.count("ZKBio Person", {"server": self.code, "missing_in_source": 0})
		self.stats["guard"]["в реестре"] = actual
		self.stats["guard"]["в выгрузке"] = received
		if actual < cint(self.settings.guard_min_records):
			return
		if received < actual * (1 - threshold / 100):
			shrink = round(100 * (actual - received) / actual, 1)
			raise ZKBioGuardTripped(
				f"Загрузка сервера {self.code} остановлена предохранителем: персонала в СКУД {received}, "
				f"актуальных в реестре {actual} — сокращение на {shrink}%, допустимо не более {threshold}%. "
				f"Если сокращение ожидаемое, временно поднимите «Допустимое сокращение выгрузки, %» "
				f"в Access Registry Settings до {int(shrink) + 1} или выше."
			)

	# ------------------------------------------------------------ persons

	def resolve_person(self, names: PersonMatcher, fields: dict) -> tuple:
		full_name = f"{fields['last_name']} {fields['first_name']}".strip()
		person, note = names.match(full_name)
		if person:
			return person, BY_NAME, ""
		return None, "", note

	def sync_persons(self, rows: list[dict]):
		received = [r for r in rows if r.get("id")]
		self.check_guard(len(received))
		pins = Counter(clean(r.get("pin")) for r in received)
		for pin, count in sorted(pins.items()):
			if pin and count > 1:
				self.warn(f"табельный номер (pin) {pin} встречается в выгрузке {count} раз(а)")
		existing = {
			r.name: r
			for r in frappe.get_all(
				"ZKBio Person",
				filters={"server": self.code},
				fields=["name", "src_hash", "missing_in_source", "manual_person"],
				limit_page_length=0,
			)
		}
		names = PersonMatcher()
		seen = set()
		for row in received:
			name = self.key(row["id"])
			seen.add(name)
			fields = {
				"server": self.code,
				"zkbio_id": clean(row.get("id")),
				"pin": clean(row.get("pin")),
				"last_name": clean(row.get("last_name")),
				"first_name": clean(row.get("first_name")),
				"email": clean(row.get("email")),
				"dept_name": clean(row.get("dept_name")),
				"hire_date": date_or_none(row.get("hire_date")),
				"zk_status": cint(row.get("status")),
				"missing_in_source": 0,
			}
			current = existing.get(name)
			link = None
			if not (current and current.manual_person):
				link = self.resolve_person(names, fields)
			h = src_hash({"f": fields, "l": link, "v": DERIVED_VERSION})
			if current and current.src_hash == h and not current.missing_in_source:
				self.stats["ZKBio Person"]["unchanged"] += 1
				continue
			values = dict(fields)
			values["src_hash"] = h
			if link:
				values["person"] = link[0] or ""
				values["person_link_method"] = link[1]
				values["person_link_note"] = link[2]
			if current:
				doc = frappe.get_doc("ZKBio Person", name)
				doc.update(values)
				if save_changed(doc):
					self.stats["ZKBio Person"]["updated"] += 1
				else:
					self.stats["ZKBio Person"]["unchanged"] += 1
			else:
				doc = frappe.new_doc("ZKBio Person")
				doc.update(values)
				doc.record_key = name
				doc.insert(ignore_permissions=True)
				self.stats["ZKBio Person"]["created"] += 1
		for name, row in existing.items():
			if name not in seen and not row.missing_in_source:
				doc = frappe.get_doc("ZKBio Person", name)
				doc.missing_in_source = 1
				doc.save(ignore_permissions=True, ignore_version=False)
				self.stats["ZKBio Person"]["missing"] += 1

	# ------------------------------------------------------------ events

	def run_events(self, fetch):
		"""Watermark loop: events strictly after the watermark (minus the overlap).

		First run starts 180 days back — the retention window, older history is purged anyway.
		"""
		overlap = cint(self.server.events_overlap_minutes) or 10
		watermark = get_datetime(self.server.events_watermark) if self.server.events_watermark else None
		if watermark:
			since = watermark - timedelta(minutes=overlap)
		else:
			since = get_datetime(add_days(getdate(), -180))
		while True:
			rows = fetch(self.server, since, 5000)
			if not rows:
				break
			self.sync_events(rows)
			since = max(get_datetime(r["event_time"]) for r in rows)
			watermark = since
			if len(rows) < 5000:
				break
		if watermark:
			frappe.db.set_value(
				"ZKBio Server", self.code, "events_watermark", watermark, update_modified=False
			)

	def sync_events(self, rows: list[dict]):
		allowed = pass_nos(self.server)
		pin_map = {}
		for r in frappe.get_all(
			"ZKBio Person",
			filters={"server": self.code, "missing_in_source": 0},
			fields=["name", "pin", "person"],
			limit_page_length=0,
		):
			pin = clean(r.pin)
			if pin:
				pin_map.setdefault(pin, (r.name, r.person))
		for row in rows:
			stats = self.stats["ZKBio Event"]
			pin = clean(row.get("pin"))
			if not pin:
				stats["skipped_no_pin"] += 1
				continue
			if cint(row.get("event_no")) not in allowed:
				stats["skipped_event_no"] += 1
				continue
			name = self.key(row["id"])
			if frappe.db.exists("ZKBio Event", name):
				stats["duplicates"] += 1
				continue
			zkbio_person, person = pin_map.get(pin, ("", ""))
			doc = frappe.new_doc("ZKBio Event")
			doc.update(
				{
					"record_key": name,
					"server": self.code,
					"zkbio_id": clean(row.get("id")),
					"event_time": get_datetime(row.get("event_time")),
					"zkbio_person": zkbio_person,
					"person": person or "",
					"pin": pin,
					"card_no": clean(row.get("card_no")),
					"dev_alias": clean(row.get("dev_alias")),
					"event_point_name": clean(row.get("event_point_name")),
					"reader_name": clean(row.get("reader_name")),
					"event_no": cint(row.get("event_no")),
					"event_name": clean(row.get("event_name")),
					"verify_mode_name": clean(row.get("verify_mode_name")),
					"acc_zone": clean(row.get("acc_zone")),
					"area_name": clean(row.get("area_name")),
				}
			)
			doc.insert(ignore_permissions=True)
			stats["created"] += 1
