"""Upload endpoint for the Synology collector."""

import frappe
from frappe import _

from access_registry.permissions import require

UPLOAD_ROLES = ("1C Sync",)  # the technical role of collectors and n8n; administrators always pass


@frappe.whitelist(methods=["POST"])
def upload(server: str):
	"""Body: the collector output (text, optionally gzip). Answers with the result of the load."""
	require(*UPLOAD_ROLES)
	if not frappe.db.exists("File Server", server):
		frappe.throw(_("Сервер {0} не заведён в «Файловые серверы»").format(server), frappe.DoesNotExistError)
	data = frappe.request.get_data() if frappe.request else b""
	if not data:
		frappe.throw(_("Пустая выгрузка"))
	from access_registry.file_shares.sync import run_upload

	log = run_upload(server, data, commit=not frappe.flags.in_test)  # tests run in a rolled back transaction
	return {"status": log.status, "log": log.name, "stats": frappe.parse_json(log.stats or "{}")}
