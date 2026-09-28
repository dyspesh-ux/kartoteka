# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.reports import role_mining


def execute(filters=None):
	return role_mining(filters)
