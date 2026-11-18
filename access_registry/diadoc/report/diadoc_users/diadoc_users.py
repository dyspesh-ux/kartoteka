# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe

from access_registry.diadoc.report.diadoc_common import COMMON_COLUMNS, FLAG_COLUMNS, column, users_query


def execute(filters=None):
	"""Пользователи Диадок по последней выгрузке; пропавшие из выгрузки не показываются."""
	sql, params = users_query(filters)
	rows = frappe.db.sql(sql, params, as_dict=True)
	persons = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", [r.person for r in rows if r.person] or [""]]},
			fields=["name", "full_name"],
			as_list=True,
			limit_page_length=0,
		)
	)
	for row in rows:
		row["person_name"] = persons.get(row.person) or ""
	columns = (
		COMMON_COLUMNS
		+ FLAG_COLUMNS
		+ [
			column("person_name", "Сотрудник", 200),
			column("person", "Карточка", 140, "Link", "Person"),
			column("person_link_method", "Как найден сотрудник", 140),
		]
	)
	return columns, rows
