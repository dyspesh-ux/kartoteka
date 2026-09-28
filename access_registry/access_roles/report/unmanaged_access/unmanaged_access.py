# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.reports import unmanaged_access


def execute(filters=None):
	return unmanaged_access(filters)
