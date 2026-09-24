"""Helpers for the HR Department tree: the single root and organisation nodes."""

import frappe

ROOT_KEY = "ROOT"
ROOT_TITLE = "Все организации"


def ensure_root() -> str:
	if not frappe.db.exists("HR Department", ROOT_KEY):
		doc = frappe.new_doc("HR Department")
		doc.record_key = ROOT_KEY
		doc.title = ROOT_TITLE
		doc.node_type = "Корень"
		doc.is_group = 1
		doc.insert(ignore_permissions=True)
	return ROOT_KEY


def org_node_key(source_code: str, org_guid: str) -> str:
	return f"{source_code}:org:{org_guid}"
