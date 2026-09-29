# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.suppression import hide_in_report
from access_registry.bitrix24.reports import profile_differences


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report(
		"quality",
		profile_differences(filters),
		filters,
		key=lambda r: (
			f"quality|Битрикс24: {r.get('field')}|{r.get('person') or ''}|{r.get('user_name') or ''}"
		),
	)
