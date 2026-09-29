# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report
from access_registry.access_roles.suppression import hide_in_report


def _execute(filters=None):
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
		extra_fields="u.extra_roles, u.person, p.full_name as person_name",
		join="left join `tabPerson` p on p.name = u.person",
	)


def extra_roles_key(row):
	"""The same key as the alert «роли в обход профилей» of «Привилегированный доступ» in the app."""
	title = _("1С {0}: роли в обход профилей").format(row.get("base_code"))
	return f"privileged|{row.get('person') or ''}|{row.get('person_name') or _('(не привязан)')}|{title}"


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report("privileged", _execute(filters), filters, key=extra_roles_key)
