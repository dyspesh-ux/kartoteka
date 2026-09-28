# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.bitrix24.reports import active_not_working


def execute(filters=None):
	return active_not_working(filters)
