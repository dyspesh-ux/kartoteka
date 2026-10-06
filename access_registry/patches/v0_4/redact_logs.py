"""Secrets out of the logs written before redaction existed (Bitrix24 webhook URLs in network errors)."""

import frappe

from access_registry.redact import redact

TABLES = (("Sync Log", "messages"), ("B24 Write Log", "error"), ("Error Log", "error"))


def execute():
	for doctype, field in TABLES:
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, field):
			continue
		for name, text in frappe.db.sql(
			f"select name, `{field}` from `tab{doctype}` where `{field}` like '%%/rest/%%' or `{field}` like '%%token=%%'"
		):
			clean = redact(text)
			if clean != text:
				frappe.db.set_value(doctype, name, field, clean, update_modified=False)
