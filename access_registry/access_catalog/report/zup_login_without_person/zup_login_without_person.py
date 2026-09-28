# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_catalog.report_utils import user_report


def execute(filters=None):
	"""Вход разрешён, а физлицо в карточке пользователя 1С не указано."""
	return user_report(filters, "u.login_allowed = 1 and ifnull(u.person_id, '') = ''")
