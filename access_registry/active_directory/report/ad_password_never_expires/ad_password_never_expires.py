# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.active_directory.report_utils import account_report


def execute(filters=None):
	"""Включённые учётки с паролем, который не истекает."""
	return account_report(
		filters,
		"a.enabled = 1 and a.password_never_expires = 1",
		extra_columns=[
			{
				"fieldname": "password_last_set",
				"label": _("Пароль сменён"),
				"fieldtype": "Datetime",
				"width": 150,
			},
			{"fieldname": "full_name", "label": _("Сотрудник"), "fieldtype": "Data", "width": 220},
		],
		extra_fields="a.password_last_set, p.full_name",
		join="left join `tabPerson` p on p.name = a.person",
	)
