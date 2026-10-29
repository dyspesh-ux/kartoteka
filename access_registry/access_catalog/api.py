"""HTTP API of the access catalog (for n8n or any other transport).

The same import runs when Frappe pulls the data itself (see ``pull.py``).
"""

import frappe

from access_registry.access_catalog import importer
from access_registry.access_catalog.pull import _finish_log, _new_log
from access_registry.permissions import require_source_sender

IMPORT_ROLES = ["System Manager", "Registry Admin", "1C Sync"]


def _commit():
	# Tests run inside a transaction that is rolled back afterwards.
	if not frappe.flags.in_test:
		frappe.db.commit()


def _check(base_code):
	frappe.only_for(IMPORT_ROLES)
	require_source_sender("Info Base", importer.resolve_base_code(base_code))


def _journal(base_code, kind, status, stats, messages=()):
	"""Data pushed through the API is journaled like a pull, with the sender."""
	log = _new_log(importer.resolve_base_code(base_code), kind)
	_finish_log(log, status, {**stats, "via": "API", "sender": frappe.session.user}, list(messages))
	return log


def _guarded(title, func, *args, kind=None, base_code=None):
	try:
		return func(*args)
	except Exception as e:
		if not frappe.flags.in_test:
			frappe.db.rollback()
		frappe.log_error(title=title)
		if kind and not isinstance(e, frappe.DoesNotExistError):  # an unknown base has no journal
			status = "Остановлен предохранителем" if isinstance(e, importer.CatalogGuardTripped) else "Ошибка"
			_journal(base_code, kind, status, {}, [str(e)])
			_commit()
		raise


@frappe.whitelist(methods=["POST"])
def import_snapshot(base_code: str, snapshot: dict | str) -> dict:
	_check(base_code)

	def run():
		result, warnings = importer.import_snapshot_data(base_code, importer.parse_json_arg(snapshot))
		_journal(base_code, "Права 1С", "Успех", result, warnings)
		_commit()
		return result

	return _guarded(f"import_snapshot {base_code}", run, kind="Права 1С", base_code=base_code)


@frappe.whitelist(methods=["GET"])
def get_log_cursor(base_code: str) -> dict:
	_check(base_code)
	return _guarded(f"get_log_cursor {base_code}", importer.log_cursor, base_code)


@frappe.whitelist(methods=["POST"])
def import_log(base_code: str, payload: dict | str) -> dict:
	_check(base_code)

	def run():
		result = importer.import_log_data(base_code, importer.parse_json_arg(payload))
		if result["inserted"]:  # an empty push every few minutes would flood the journal
			_journal(base_code, "Журнал 1С", "Успех", result)
		_commit()
		return result

	return _guarded(f"import_log {base_code}", run, kind="Журнал 1С", base_code=base_code)
