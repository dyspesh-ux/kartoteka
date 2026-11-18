# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe


def column(fieldname, label, width=110, fieldtype="Data", options=None):
	col = {"fieldname": fieldname, "label": label, "width": width, "fieldtype": fieldtype}
	if options:
		col["options"] = options
	return col


FLAG_COLUMNS = [
	column("login_active", "УЗ активна", 80),
	column("is_admin", "Админ", 70),
	column("blocked", "Заблок.", 70),
	column("can_sign", "Подпись", 70),
	column("gov_key", "Госключ", 70),
	column("can_approve", "Согл.", 70),
	column("can_request_approve", "Запрос согл.", 90),
	column("drafts", "Черновики", 80),
	column("can_delete", "Удаление", 80),
	column("payments", "Платёжки", 80),
	column("counterparties", "Контрагенты", 90),
]

COMMON_COLUMNS = [
	column("organization", "Организация", 160),
	column("full_name", "ФИО", 200),
	column("position", "Должность", 160),
	column("login", "Логин", 180),
]


def users_query(filters=None, only_admins=False):
	filters = frappe._dict(filters or {})
	conditions = ["u.missing_in_source = 0"]
	params = {}
	if filters.get("organization"):
		conditions.append("u.organization = %(organization)s")
		params["organization"] = filters.organization
	if only_admins:
		conditions.append("u.is_admin = 1 and u.login_active = 1 and u.blocked = 0")
	return (
		f"""select u.organization, u.full_name, u.position, u.login, u.person,
		u.person_link_method, u.person_link_note, u.missing_in_source,
		u.login_active, u.is_admin, u.blocked, u.can_sign, u.gov_key, u.can_approve,
		u.can_request_approve, u.drafts, u.can_delete, u.payments, u.counterparties
		from `tabDiadoc User` u
		where {" and ".join(conditions)}
		order by u.organization, u.full_name""",
		params,
	)
