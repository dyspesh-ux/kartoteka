# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.business_processes.reports import continuity


def execute(filters=None):
	return continuity(filters)
