# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.business_processes.reports import participants


def execute(filters=None):
	return participants(filters)
