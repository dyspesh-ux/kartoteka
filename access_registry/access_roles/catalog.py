"""Filling the entitlement catalog from the mirrored systems, and the role model import endpoints."""

import frappe
from frappe.utils import cint

from access_registry.access_roles import engine
from access_registry.app_access import WORK, require_section

CANDIDATE_LIMIT = 500


@frappe.whitelist()
def catalog_candidates(min_holders: int = 1, system: str | None = None) -> list:
	"""Rights the employees have but the catalog does not describe yet (per system mirror)."""
	require_section("roles")
	_actual, other = engine.actual_entitlements()
	holders = {}
	for _person, keys in other.items():
		for key in keys:
			holders[key] = holders.get(key, 0) + 1
	rows = []
	for key, count in holders.items():
		if count < cint(min_holders) or count < 1:
			continue
		info = engine.describe_key(key)
		if system and info["system"] != system:
			continue
		rows.append({"key": key, "title": info["title"], "system": info["system"], "holders": count})
	rows.sort(key=lambda r: (-r["holders"], r["title"]))
	return rows[:CANDIDATE_LIMIT]


@frappe.whitelist(methods=["POST"])
def catalog_add(keys) -> list:
	"""Adds the chosen raw keys to the catalog (creates Entitlement cards bound to the systems)."""
	require_section("roles", WORK)
	if isinstance(keys, str):
		keys = frappe.parse_json(keys)
	created = [engine.ensure_entitlement(key) for key in keys or []]
	return created


@frappe.whitelist()
def download_template():
	"""The role model workbook of the current moment: edit it and load it back."""
	require_section("roles", WORK)
	from access_registry.access_roles.xlsx_io import template_workbook

	frappe.local.response.filename = "role_model.xlsx"
	frappe.local.response.filecontent = template_workbook()
	frappe.local.response.type = "download"
