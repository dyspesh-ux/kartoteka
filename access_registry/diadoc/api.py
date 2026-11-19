"""Upload endpoints of the Diadoc module (called from desk and from the /registry app)."""

import binascii
from base64 import b64decode

import frappe
from frappe import _

from access_registry.permissions import ADMINS, DD_SPECIALIST, require


@frappe.whitelist(methods=["POST"])
def upload(content: str, file_name: str | None = None) -> dict:
	"""Accepts one xlsx export (base64), mirrors it and returns the Sync Log result.

	Usable without an organization card: the organizations in the file are created
	automatically by the sync.
	"""
	require(*ADMINS, DD_SPECIALIST)
	if not content:
		frappe.throw(_("Пустой файл"))
	try:
		data = b64decode(content, validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("Файл не прочитан: повреждённые данные (нужна выгрузка xlsx из Диадок)"))
	from access_registry.diadoc.sync import run_upload

	log = run_upload(data, commit=not frappe.flags.in_test, file_name=file_name)
	return {"status": log.status, "log": log.name, "stats": frappe.parse_json(log.stats or "{}")}
