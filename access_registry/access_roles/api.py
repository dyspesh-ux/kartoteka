"""Actions of the role model called from forms and reports."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from access_registry.access_roles import engine
from access_registry.permissions import ROLE_MANAGER, require


@frappe.whitelist(methods=["POST"])
def create_draft_roles(min_people=3, threshold=80, positions=None):
	"""Draft job roles from the current accesses (initial reconciliation)."""
	require(ROLE_MANAGER)
	if isinstance(positions, str):
		positions = frappe.parse_json(positions) if positions.startswith("[") else [positions]
	created = engine.create_draft_roles(cint(min_people) or 3, (flt(threshold) or 80) / 100, positions)
	return {
		"created": created,
		"message": _("Создано черновиков ролей: {0}. Проверьте их и переведите в «Действует».").format(
			len(created)
		)
		if created
		else _("Новых черновиков нет: для всех подходящих должностей роли уже есть."),
	}


@frappe.whitelist(methods=["POST"])
def refresh_counters():
	require(ROLE_MANAGER)
	engine.refresh_counters()
	return _("Счётчики пересчитаны.")


@frappe.whitelist(methods=["POST"])
def add_to_catalog(keys):
	"""Creates entitlements for raw access keys (from «Доступы вне каталога»)."""
	require(ROLE_MANAGER)
	if isinstance(keys, str):
		keys = frappe.parse_json(keys)
	return [engine.ensure_entitlement(key) for key in keys or []]


def scheduled_refresh():
	engine.refresh_counters()
