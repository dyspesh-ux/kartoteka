# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.reports import sod_conflicts


def execute(filters=None):
	return sod_conflicts(filters)
