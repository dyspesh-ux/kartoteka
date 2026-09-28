# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.active_directory.report_utils import account_report


def execute(filters=None):
	"""Прямые участники-учётки выбранной группы AD и статус сотрудника."""
	filters = dict(filters or {})
	if not filters.get("group"):
		return [], []
	return account_report(
		{},
		"ag.`group` = %(group)s",
		extra_columns=[
			{"fieldname": "full_name", "label": _("Сотрудник"), "fieldtype": "Data", "width": 220},
			{"fieldname": "person_status", "label": _("Статус"), "fieldtype": "Data", "width": 110},
		],
		extra_fields="p.full_name, p.status as person_status",
		join="join `tabAD Account Group` ag on ag.parent = a.name and ag.parenttype = 'AD Account' "
		"left join `tabPerson` p on p.name = a.person",
		params={"group": filters["group"]},
	)
