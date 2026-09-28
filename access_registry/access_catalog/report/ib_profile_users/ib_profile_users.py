# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report


def execute(filters=None):
	"""Пользователи выбранного профиля доступа."""
	filters = dict(filters or {})
	if not filters.get("profile"):
		return [], []
	return user_report(
		filters,
		"up.profile = %(profile)s",
		extra_columns=[
			{
				"fieldname": "access_group_name",
				"label": _("Группа доступа"),
				"fieldtype": "Data",
				"width": 200,
			},
			{"fieldname": "direct", "label": _("Напрямую"), "fieldtype": "Check", "width": 80},
			{"fieldname": "via_name", "label": _("Через"), "fieldtype": "Data", "width": 200},
			{"fieldname": "orgs_text", "label": _("Организации"), "fieldtype": "Data", "width": 250},
		],
		extra_fields="up.access_group_name, up.direct, up.via_name, up.orgs_text",
		join="join `tabIB User Profile` up on up.parent = u.name and up.parenttype = 'IB User'",
		params={"profile": filters["profile"]},
	)
