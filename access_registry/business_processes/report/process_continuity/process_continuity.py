# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.suppression import hide_in_report
from access_registry.business_processes.reports import continuity


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report("processes", continuity(filters), filters)
