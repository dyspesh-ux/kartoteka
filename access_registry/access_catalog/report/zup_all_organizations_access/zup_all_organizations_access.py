# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_catalog.report_utils import user_report


def execute(filters=None):
	"""Вход разрешён и доступ ко всем организациям."""
	return user_report(filters, "u.all_orgs = 1 and u.login_allowed = 1")
