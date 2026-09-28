# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, getdate


def execute(filters=None):
	"""Кто и когда менял пользователей и права в 1С (журнал регистрации)."""
	filters = frappe._dict(filters or {})
	conditions, params = [], {}
	if filters.base_code:
		conditions.append("base_code = %(base_code)s")
		params["base_code"] = filters.base_code
	if filters.from_date:
		conditions.append("event_date >= %(from_date)s")
		params["from_date"] = getdate(filters.from_date)
	if filters.to_date:
		conditions.append("event_date < %(to_date)s")
		params["to_date"] = add_days(getdate(filters.to_date), 1)
	if filters.who:
		conditions.append("who like %(who)s")
		params["who"] = f"%{filters.who}%"
	if filters.object_type:
		conditions.append("object_type = %(object_type)s")
		params["object_type"] = filters.object_type
	where = ("where " + " and ".join(conditions)) if conditions else ""
	data = frappe.db.sql(
		f"""select event_date, who, who_user, event_title, object_type, object, host, comment, base_code
		from `tabIB Audit Event` {where} order by event_date desc""",
		params,
		as_dict=True,
	)
	columns = [
		{"fieldname": "event_date", "label": _("Когда"), "fieldtype": "Datetime", "width": 160},
		{"fieldname": "who", "label": _("Кто"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "who_user",
			"label": _("Пользователь 1С"),
			"fieldtype": "Link",
			"options": "IB User",
			"width": 160,
		},
		{"fieldname": "event_title", "label": _("Событие"), "fieldtype": "Data", "width": 190},
		{"fieldname": "object_type", "label": _("Тип объекта"), "fieldtype": "Data", "width": 220},
		{"fieldname": "object", "label": _("Объект"), "fieldtype": "Data", "width": 220},
		{"fieldname": "host", "label": _("Компьютер"), "fieldtype": "Data", "width": 120},
		{"fieldname": "comment", "label": _("Комментарий"), "fieldtype": "Data", "width": 200},
		{
			"fieldname": "base_code",
			"label": _("База"),
			"fieldtype": "Link",
			"options": "Info Base",
			"width": 80,
		},
	]
	return columns, data
