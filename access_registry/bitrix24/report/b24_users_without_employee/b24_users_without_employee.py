# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.bitrix24.reports import without_employee


def execute(filters=None):
	return without_employee(filters)
