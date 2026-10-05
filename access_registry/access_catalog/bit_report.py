"""Rights of the treasury (BIT.Finance) in 1C: visas, executor roles and access by CFO and cash flow item.

One row per right of a 1C user, with the employee and where they work. Loaded with the snapshot of
rights (section «bit»), see importer.bit_rows.
"""

import frappe
from frappe import _

KINDS = ("Виза", "Роль исполнителя", "Доступ к ЦФО")


def bit_rights(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["b.parenttype = 'IB User'", "u.missing_in_source = 0"]
	params = {}
	if not filters.include_disabled:
		conditions.append("u.login_allowed = 1 and u.invalid = 0")
	for key, column in (("base_code", "u.base_code"), ("kind", "b.kind"), ("person", "u.person")):
		if filters.get(key):
			conditions.append(f"{column} = %({key})s")
			params[key] = filters.get(key)
	if filters.get("cfo"):
		conditions.append("(b.cfo like %(cfo)s or b.object like %(cfo)s)")
		params["cfo"] = f"%{filters.cfo}%"
	if filters.get("right_name"):
		conditions.append("b.right_name like %(right_name)s")
		params["right_name"] = f"%{filters.right_name}%"
	if filters.get("only_not_working"):
		conditions.append("ifnull(p.status, '') != 'Работает'")
	rows = frappe.db.sql(
		f"""select u.name as user, u.user_name, u.base_code, u.login, u.login_allowed, u.person,
			p.full_name, p.status as person_status,
			b.kind, b.right_name, b.object, b.access, b.condition, b.details, b.direct, b.assigned_to,
			b.deputy_for
		from `tabIB User BIT Right` b
			join `tabIB User` u on u.name = b.parent
			left join `tabPerson` p on p.name = u.person
		where {" and ".join(conditions)}
		order by coalesce(p.full_name, u.user_name), u.base_code, field(b.kind, 'Виза', 'Роль исполнителя',
			'Доступ к ЦФО'), b.right_name""",
		params,
		as_dict=True,
	)
	for r in rows:
		r.employee = r.full_name or ""
	return columns(), rows


def columns():
	return [
		{"fieldname": "employee", "label": _("Сотрудник"), "fieldtype": "Data", "width": 200},
		{"fieldname": "kind", "label": _("Вид"), "fieldtype": "Data", "width": 130},
		{"fieldname": "right_name", "label": _("Виза, роль или ЦФО"), "fieldtype": "Data", "width": 200},
		{"fieldname": "object", "label": _("Объект"), "fieldtype": "Data", "width": 180},
		{"fieldname": "access", "label": _("Доступ"), "fieldtype": "Data", "width": 120},
		{"fieldname": "condition", "label": _("Условие визы"), "fieldtype": "Data", "width": 170},
		{"fieldname": "deputy_for", "label": _("Замещает"), "fieldtype": "Data", "width": 140},
		{"fieldname": "person_status", "label": _("Статус"), "fieldtype": "Data", "width": 100},
		{"fieldname": "user_name", "label": _("Пользователь 1С"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "base_code",
			"label": _("База"),
			"fieldtype": "Link",
			"options": "Info Base",
			"width": 80,
		},
		{"fieldname": "assigned_to", "label": _("Кому назначено"), "fieldtype": "Data", "width": 160},
		{"fieldname": "details", "label": _("Подробно"), "fieldtype": "Data", "width": 220},
		{"fieldname": "login_allowed", "label": _("Вход"), "fieldtype": "Check", "width": 60},
		{
			"fieldname": "user",
			"label": _("Карточка пользователя"),
			"fieldtype": "Link",
			"options": "IB User",
			"width": 150,
		},
		{
			"fieldname": "person",
			"label": _("Карточка сотрудника"),
			"fieldtype": "Link",
			"options": "Person",
			"width": 150,
		},
	]
