# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.suppression import by_ref, hide_in_report
from access_registry.bitrix24.reports import without_employee


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report(
		"unlinked", without_employee(filters), filters, key=by_ref("unlinked", "B24 User", "user")
	)
