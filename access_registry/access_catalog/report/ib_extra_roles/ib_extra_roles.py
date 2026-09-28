# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report


def execute(filters=None):
	"""Роли в обход профилей: роли пользователя ИБ, которых нет ни в одном его профиле."""
	return user_report(
		filters,
		"u.has_extra_roles = 1",
		extra_columns=[
			{
				"fieldname": "extra_roles",
				"label": _("Роли в обход профилей"),
				"fieldtype": "Small Text",
				"width": 400,
			}
		],
		extra_fields="u.extra_roles",
	)
