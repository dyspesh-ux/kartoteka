# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.active_directory.report_utils import account_report


def execute(filters=None):
	"""Учётка AD включена, а сотрудник по кадровым данным не работает."""
	return account_report(
		filters,
		"a.enabled = 1 and ifnull(a.person, '') != '' and ifnull(p.status, '') != 'Работает'",
		extra_columns=[
			{"fieldname": "full_name", "label": _("Сотрудник"), "fieldtype": "Data", "width": 220},
			{"fieldname": "person_status", "label": _("Статус"), "fieldtype": "Data", "width": 110},
			{"fieldname": "person_link_method", "label": _("Как найден"), "fieldtype": "Data", "width": 130},
		],
		extra_fields="p.full_name, p.status as person_status, a.person_link_method",
		join="left join `tabPerson` p on p.name = a.person",
	)
