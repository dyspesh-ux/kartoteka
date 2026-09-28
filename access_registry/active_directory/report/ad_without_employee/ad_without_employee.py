# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.active_directory.report_utils import account_report


def execute(filters=None):
	"""Включённые учётки AD, не привязанные к сотруднику: служебные, внешние или не найденные."""
	return account_report(
		filters,
		"a.enabled = 1 and ifnull(a.person, '') = ''",
		extra_columns=[
			{
				"fieldname": "person_link_note",
				"label": _("Почему не найден"),
				"fieldtype": "Data",
				"width": 320,
			},
			{"fieldname": "employee_number", "label": _("employeeNumber"), "fieldtype": "Data", "width": 150},
		],
		extra_fields="a.person_link_note, a.employee_number",
	)
