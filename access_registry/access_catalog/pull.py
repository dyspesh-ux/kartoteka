"""Frappe pulls the rights snapshot and the event log from the ITAccess HTTP service itself."""

import json
import traceback

import frappe
import requests
from frappe.utils import now_datetime

from access_registry.access_catalog import importer
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user

SAVEPOINT = "access_catalog_import"


class ITAccessError(Exception):
	pass


# --------------------------------------------------------------------------- scheduler


def _sources():
	return frappe.get_all("Info Base", filters={"enabled": 1, "itaccess_enabled": 1}, pluck="name")


def scheduled_snapshot():
	for source in _sources():
		enqueue(source, "snapshot")


def scheduled_log():
	for source in _sources():
		enqueue(source, "log")


def job_id_for(source: str, kind: str) -> str:
	return f"access_catalog_{kind}::{source}"


def enqueue(source: str, kind: str):
	method = {"snapshot": run_snapshot, "log": run_log}[kind]
	return frappe.enqueue(
		f"access_registry.access_catalog.pull.{method.__name__}",
		queue="long",
		timeout=get_settings().job_timeout,
		job_id=job_id_for(source, kind),
		deduplicate=True,
		source=source,
	)


# --------------------------------------------------------------------------- HTTP


def fetch(source, path: str, params=None, timeout: int = 300):
	url = (source.itaccess_url or "").rstrip("/") + "/" + path
	if not source.itaccess_url:
		raise ITAccessError(f"У источника {source.name} не указан URL сервиса ITAccess")
	password = (
		source.get_password("itaccess_password", raise_exception=False) if source.itaccess_password else ""
	)
	response = requests.get(
		url,
		params=params,
		auth=(source.itaccess_username or "", password or ""),
		verify=bool(source.verify_ssl),
		timeout=timeout,
		headers={"Accept": "application/json"},
	)
	response.encoding = "utf-8"
	try:
		body = response.json()
	except ValueError:
		body = None
	if response.status_code != 200:
		detail = body.get("error") if isinstance(body, dict) and body.get("error") else response.text[:1000]
		raise ITAccessError(f"GET {url} → HTTP {response.status_code}: {detail}")
	if not isinstance(body, dict):
		raise ITAccessError(f"GET {url}: ответ не JSON-объект: {response.text[:300]}")
	return body


# --------------------------------------------------------------------------- jobs


def _new_log(source, kind):
	return frappe.get_doc(
		{
			"doctype": "Sync Log",
			"source": source,
			"kind": kind,
			"status": "В процессе",
			"started": now_datetime(),
		}
	).insert(ignore_permissions=True)


def _finish_log(log, status, stats, messages, meta=None):
	log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps(stats, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	if meta is not None:
		log.meta_snapshot = json.dumps(meta, ensure_ascii=False, indent=1)
	log.save(ignore_permissions=True)


def run_snapshot(source: str, commit: bool = True, fetch_snapshot=None):
	"""Downloads /snapshot and imports it in one transaction. Returns the Sync Log."""
	settings = get_settings()
	log = _new_log(source, "Права 1С")
	if commit:
		frappe.db.commit()
	stats, messages, meta = {}, [], None
	previous_user = frappe.session.user
	_switch_user(settings.sync_user, messages)
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc = frappe.get_doc("Info Base", source)
		data = (fetch_snapshot or fetch)(doc, "snapshot", timeout=settings.http_timeout)
		meta = {k: data.get(k) for k in ("base", "generated_at") if k in data}
		stats, warnings = importer.import_snapshot_data(source, data)
		messages += warnings
		status = "Успех"
		frappe.db.set_value(
			"Info Base", source, "itaccess_last_snapshot", now_datetime(), update_modified=False
		)
	except importer.CatalogGuardTripped as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except Exception:
		messages.append(traceback.format_exc())
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Ошибка"
	finally:
		_switch_user(previous_user, None)
	_finish_log(log, status, stats, messages, meta)
	frappe.db.set_value(
		"Info Base",
		source,
		"itaccess_last_status",
		f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})",
		update_modified=False,
	)
	if commit:
		frappe.db.commit()
	return log


def run_log(source: str, commit: bool = True, fetch_log=None):
	"""Downloads /log since the cursor. A Sync Log is written only when events arrived or on error,
	otherwise a run every 15 minutes would flood the journal."""
	settings = get_settings()
	previous_user = frappe.session.user
	_switch_user(settings.sync_user, None)
	frappe.db.savepoint(SAVEPOINT)
	try:
		doc = frappe.get_doc("Info Base", source)
		cursor = importer.log_cursor(source)["from"]
		data = (fetch_log or fetch)(doc, "log", params={"from": cursor}, timeout=settings.http_timeout)
		result = importer.import_log_data(source, data)
		frappe.db.set_value("Info Base", source, "itaccess_last_log", now_datetime(), update_modified=False)
		if result["inserted"]:
			log = _new_log(source, "Журнал 1С")
			_finish_log(log, "Успех", {**result, "from": cursor}, [])
		if commit:
			frappe.db.commit()
		return result
	except Exception:
		error = traceback.format_exc()
		frappe.db.rollback(save_point=SAVEPOINT)
		log = _new_log(source, "Журнал 1С")
		_finish_log(log, "Ошибка", {}, [error])
		if commit:
			frappe.db.commit()
		return {"error": error}
	finally:
		_switch_user(previous_user, None)
