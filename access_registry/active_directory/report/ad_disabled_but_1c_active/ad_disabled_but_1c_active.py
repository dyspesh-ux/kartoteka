# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	"""Учётка AD отключена или удалена, а вход в 1С по ней разрешён."""
	filters = frappe._dict(filters or {})
	conditions = [
		"u.login_allowed = 1",
		"u.invalid = 0",
		"u.missing_in_source = 0",
		"(a.enabled = 0 or a.missing_in_source = 1)",
	]
	params = {}
	if filters.domain:
		conditions.append("a.domain = %(domain)s")
		params["domain"] = filters.domain
	data = frappe.db.sql(
		f"""select u.name, u.user_name, u.base_code, u.login, u.ad_login, a.name as ad_account,
			a.domain, a.enabled, a.missing_in_source as ad_missing, a.last_logon
		from `tabIB User` u join `tabAD Account` a on a.name = u.ad_account
		where {" and ".join(conditions)} order by u.user_name""",
		params,
		as_dict=True,
	)
	columns = [
		{"fieldname": "user_name", "label": _("Пользователь 1С"), "fieldtype": "Data", "width": 240},
		{
			"fieldname": "base_code",
			"label": _("База"),
			"fieldtype": "Link",
			"options": "Info Base",
			"width": 90,
		},
		{"fieldname": "login", "label": _("Логин 1С"), "fieldtype": "Data", "width": 150},
		{"fieldname": "ad_login", "label": _("Логин AD"), "fieldtype": "Data", "width": 130},
		{"fieldname": "enabled", "label": _("Учётка AD включена"), "fieldtype": "Check", "width": 120},
		{"fieldname": "ad_missing", "label": _("Нет в AD"), "fieldtype": "Check", "width": 80},
		{"fieldname": "last_logon", "label": _("Последний вход в AD"), "fieldtype": "Datetime", "width": 150},
		{
			"fieldname": "name",
			"label": _("Пользователь"),
			"fieldtype": "Link",
			"options": "IB User",
			"width": 140,
		},
		{
			"fieldname": "ad_account",
			"label": _("Учётка AD"),
			"fieldtype": "Link",
			"options": "AD Account",
			"width": 140,
		},
	]
	return columns, data
