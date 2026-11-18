# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe

from access_registry.diadoc.report.diadoc_common import COMMON_COLUMNS, column, users_query


def execute(filters=None):
	"""Администраторы организаций Диадок: только активные незаблокированные учётки."""
	sql, params = users_query(filters, only_admins=True)
	rows = frappe.db.sql(sql, params, as_dict=True)
	persons = frappe.db.sql(
		"""select name, full_name, status from `tabPerson`
		where name in %(names)s""",
		{"names": [r.person for r in rows if r.person] or [""]},
		as_dict=True,
	)
	by_name = {p.name: p for p in persons}
	for row in rows:
		person = by_name.get(row.person)
		row["person_name"] = person.full_name if person else ""
		row["person_status"] = person.status if person else ""
	columns = COMMON_COLUMNS + [
		column("person_name", "Сотрудник", 200),
		column("person_status", "Статус", 110),
		column("person", "Карточка", 140, "Link", "Person"),
	]
	return columns, rows
