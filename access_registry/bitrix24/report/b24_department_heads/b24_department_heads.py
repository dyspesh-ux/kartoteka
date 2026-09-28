# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.bitrix24.reports import department_heads


def execute(filters=None):
	return department_heads(filters)
