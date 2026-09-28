# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.bitrix24.reports import section_access


def execute(filters=None):
	return section_access(filters)
