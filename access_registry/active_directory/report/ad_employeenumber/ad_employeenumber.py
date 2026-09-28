# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.active_directory.report_utils import account_report


def execute(filters=None):
	"""Учётки, привязанные к сотруднику, у которых employeeNumber не равен UUID сотрудника.

	Реестр в AD не пишет: кнопка отчёта формирует скрипт PowerShell для администратора.
	"""
	return account_report(
		filters,
		"ifnull(a.person, '') != '' and ifnull(a.employee_number, '') != a.person",
		extra_columns=[
			{"fieldname": "full_name", "label": _("Сотрудник"), "fieldtype": "Data", "width": 220},
			{"fieldname": "person", "label": _("UUID сотрудника"), "fieldtype": "Data", "width": 290},
			{
				"fieldname": "employee_number",
				"label": _("Сейчас в employeeNumber"),
				"fieldtype": "Data",
				"width": 200,
			},
			{"fieldname": "person_link_method", "label": _("Как найден"), "fieldtype": "Data", "width": 120},
			{"fieldname": "object_guid", "label": _("objectGUID"), "fieldtype": "Data", "width": 290},
		],
		extra_fields="p.full_name, a.person, a.employee_number, a.person_link_method, a.object_guid",
		join="left join `tabPerson` p on p.name = a.person",
	)
