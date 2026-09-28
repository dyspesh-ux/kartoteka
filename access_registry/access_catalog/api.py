"""HTTP API of the access catalog (for n8n or any other transport).

The same import runs when Frappe pulls the data itself (see ``pull.py``).
"""

import frappe

from access_registry.access_catalog import importer

IMPORT_ROLES = ["System Manager", "Registry Admin", "1C Sync"]


def _commit():
	# Tests run inside a transaction that is rolled back afterwards.
	if not frappe.flags.in_test:
		frappe.db.commit()


def _guarded(title, func, *args):
	try:
		return func(*args)
	except Exception:
		if not frappe.flags.in_test:
			frappe.db.rollback()
		frappe.log_error(title=title)
		raise


@frappe.whitelist(methods=["POST"])
def import_snapshot(base_code: str, snapshot: dict | str) -> dict:
	frappe.only_for(IMPORT_ROLES)

	def run():
		result, _warnings = importer.import_snapshot_data(base_code, importer.parse_json_arg(snapshot))
		_commit()
		return result

	return _guarded(f"import_snapshot {base_code}", run)


@frappe.whitelist(methods=["GET"])
def get_log_cursor(base_code: str) -> dict:
	frappe.only_for(IMPORT_ROLES)
	return _guarded(f"get_log_cursor {base_code}", importer.log_cursor, base_code)


@frappe.whitelist(methods=["POST"])
def import_log(base_code: str, payload: dict | str) -> dict:
	frappe.only_for(IMPORT_ROLES)

	def run():
		result = importer.import_log_data(base_code, importer.parse_json_arg(payload))
		_commit()
		return result

	return _guarded(f"import_log {base_code}", run)
