# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.bitrix24.reports import profile_differences


def execute(filters=None):
	return profile_differences(filters)
