# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report


def execute(filters=None):
	"""Вход в 1С разрешён, а человек по кадровым данным не работает или не найден.

	Пользователи без физлица в карточке — в отчёте «ZUP Login Without Person».
	"""
	columns, data = user_report(
		filters,
		"u.login_allowed = 1 and ifnull(u.person_id, '') != '' "
		"and (p.name is null or ifnull(p.status, '') != 'Работает')",
		extra_columns=[
			{
				"fieldname": "person",
				"label": _("Человек"),
				"fieldtype": "Link",
				"options": "Person",
				"width": 220,
			},
			{"fieldname": "person_status", "label": _("Статус человека"), "fieldtype": "Data", "width": 130},
			{"fieldname": "reason", "label": _("Причина"), "fieldtype": "Data", "width": 300},
		],
		extra_fields="u.person, u.person_name, p.status as person_status",
		join="left join `tabPerson` p on p.name = u.person",
	)
	for row in data:
		if not row.person:
			row.reason = _("Физлицо {0} не найдено в кадровых данных базы").format(row.person_name or "")
		else:
			row.reason = _("По кадровым данным: {0}").format(row.person_status or "—")
	return columns, data
