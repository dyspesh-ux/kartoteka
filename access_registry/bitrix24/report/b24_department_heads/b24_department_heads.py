# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.suppression import hide_in_report
from access_registry.bitrix24.reports import department_heads


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report(
		"quality",
		department_heads(filters),
		filters,
		key=lambda r: f"quality|Битрикс24: руководитель||{r.get('department_name') or ''}",
	)
