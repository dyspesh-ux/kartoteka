# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe import _

from access_registry.access_catalog.report_utils import user_report
from access_registry.access_roles.suppression import by_ref, hide_in_report


def _execute(filters=None):
	"""Вход разрешён, а пользователь 1С не привязан к сотруднику.

	Служебные пользователи и сироты ИБ не люди: у них свои отчёты и отметки.
	"""
	return user_report(
		filters,
		"u.login_allowed = 1 and ifnull(u.person, '') = '' and u.service = 0 and u.is_orphan = 0",
		extra_columns=[
			{"fieldname": "person_name", "label": _("Физлицо в 1С"), "fieldtype": "Data", "width": 220},
			{
				"fieldname": "person_link_note",
				"label": _("Почему не найден"),
				"fieldtype": "Data",
				"width": 320,
			},
		],
		extra_fields="u.person_name, u.person_link_note",
	)


def execute(filters=None):
	"""Alerts suppressed in the registry app are hidden (filter «Показать погашенные» shows them)."""
	return hide_in_report("unlinked", _execute(filters), filters, key=by_ref("unlinked", "IB User"))
