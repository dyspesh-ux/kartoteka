# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report
from access_registry.access_roles.suppression import by_ref, hide_in_report


def _execute(filters=None):
	"""Вход в 1С разрешён, а сотрудник по кадровым данным не работает.

	Пользователи, не привязанные к сотруднику, — в отчёте «IB Users Without Employee».
	"""
	columns, data = user_report(
		filters,
		"u.login_allowed = 1 and ifnull(u.person, '') != '' and ifnull(p.status, '') != 'Работает'",
		extra_columns=[
			{
				"fieldname": "person",
				"label": _("Сотрудник"),
				"fieldtype": "Link",
				"options": "Person",
				"width": 220,
			},
			{
				"fieldname": "person_status",
				"label": _("Статус сотрудника"),
				"fieldtype": "Data",
				"width": 140,
			},
			{"fieldname": "person_link_method", "label": _("Как найден"), "fieldtype": "Data", "width": 150},
		],
		extra_fields="u.person, u.person_link_method, p.status as person_status",
		join="left join `tabPerson` p on p.name = u.person",
	)
	return columns, data


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report("dismissed", _execute(filters), filters, key=by_ref("dismissed", "IB User"))
