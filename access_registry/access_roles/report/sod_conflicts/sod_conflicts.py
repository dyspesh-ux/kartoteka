# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.reports import sod_conflicts
from access_registry.access_roles.suppression import hide_in_report


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report("sod", sod_conflicts(filters), filters)
