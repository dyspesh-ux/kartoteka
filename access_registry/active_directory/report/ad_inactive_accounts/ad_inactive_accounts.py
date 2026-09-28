# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _
from frappe.utils import add_days, cint, now_datetime

from access_registry.active_directory.report_utils import account_report
from access_registry.settings import get_settings


def execute(filters=None):
	"""Включённые учётки, которые давно не входили или не входили никогда."""
	filters = dict(filters or {})
	days = cint(filters.get("days")) or cint(get_settings().ad_inactive_days)
	columns, data = account_report(
		filters,
		"a.enabled = 1 and (a.last_logon is null or a.last_logon < %(since)s)",
		extra_columns=[
			{"fieldname": "days_since", "label": _("Дней без входа"), "fieldtype": "Int", "width": 110},
			{"fieldname": "full_name", "label": _("Сотрудник"), "fieldtype": "Data", "width": 220},
			{"fieldname": "person_status", "label": _("Статус"), "fieldtype": "Data", "width": 110},
		],
		extra_fields="p.full_name, p.status as person_status",
		join="left join `tabPerson` p on p.name = a.person",
		params={"since": add_days(now_datetime(), -days)},
		order="a.last_logon",
	)
	now = now_datetime()
	for row in data:
		row.days_since = (now - row.last_logon).days if row.last_logon else None
	return columns, data
