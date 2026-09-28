# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report


def execute(filters=None):
	"""Пользователи ИБ без карточки в справочнике «Пользователи»."""
	filters = dict(filters or {}, include_invalid=1)
	return user_report(
		filters,
		"u.is_orphan = 1",
		extra_columns=[
			{"fieldname": "extra_roles", "label": _("Роли ИБ"), "fieldtype": "Small Text", "width": 400}
		],
		extra_fields="u.extra_roles",
	)
