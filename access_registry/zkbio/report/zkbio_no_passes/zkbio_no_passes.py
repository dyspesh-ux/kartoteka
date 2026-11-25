# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

"""СКУД: работающие без проходов — кто должен быть на работе, но событий за период нет.

Полностью покрытые отсутствиями (отпуск/командировка на весь период) исключаются;
у частично отсутствующих видно число дней отсутствия. has_card=0 означает, что у
сотрудника нет привязанной персоны СКУД — кандидат на ручную привязку.
"""

import frappe
from frappe.utils import add_days, getdate

from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import column, department_map

NO_EVENTS_SQL = """
select p.name as person, p.full_name as person_name,
	(select max(e.event_time) from `tabZKBio Event` e where e.person = p.name) as last_event
from `tabPerson` p
where p.status = 'Работает'
	and not exists (
		select 1 from `tabZKBio Event` e
		where e.person = p.name
		and e.event_time >= %(date_from)s and e.event_time < date_add(%(date_to)s, interval 1 day)
	)
order by p.full_name
"""

ABSENCES_SQL = """
select person, date_from, coalesce(date_to, until_expected, date_from) as date_to
from `tabHR Absence`
where cancelled = 0 and person in %(persons)s
	and date_from <= %(date_to)s
	and coalesce(date_to, until_expected, date_from) >= %(date_from)s
"""


def absence_days(rows, date_from, date_to) -> dict:
	"""Person → days of the period covered by HR absences (union of intervals)."""
	days = {}
	for row in rows:
		start = max(getdate(row.date_from), date_from)
		end = min(getdate(row.date_to), date_to)
		covered = days.setdefault(row.person, set())
		day = start
		while day <= end:
			covered.add(day)
			day = add_days(day, 1)
	return {person: len(covered) for person, covered in days.items()}


def execute(filters=None):
	filters = filters or {}
	date_from, date_to = filters.get("date_from"), filters.get("date_to")
	if not date_from or not date_to:
		frappe.throw("Задайте период отчёта (даты «с» и «по»).")
	date_from, date_to = getdate(date_from), getdate(date_to)
	period_days = (date_to - date_from).days + 1
	rows = frappe.db.sql(NO_EVENTS_SQL, {"date_from": date_from, "date_to": date_to}, as_dict=True)
	if not rows:
		return _columns(), []
	persons = [r.person for r in rows]
	absent = absence_days(
		frappe.db.sql(
			ABSENCES_SQL, {"persons": persons, "date_from": date_from, "date_to": date_to}, as_dict=True
		),
		date_from,
		date_to,
	)
	with_card = set(
		frappe.get_all(
			"ZKBio Person",
			filters={"person": ["in", persons], "missing_in_source": 0},
			pluck="person",
			limit_page_length=0,
		)
	)
	departments = department_map()
	department = filters.get("department") or ""
	result = []
	for row in rows:
		row["absence_days"] = absent.get(row.person, 0)
		if row["absence_days"] >= period_days:
			continue  # отсутствует весь период — и так понятно, почему нет проходов
		dept = departments.get(row.person)
		if department and (not dept or dept[0] != department):
			continue
		row["department"] = dept[1] if dept else ""
		row["has_card"] = 1 if row.person in with_card else 0
		result.append(row)
	return _columns(), result


def _columns():
	return [
		column("person_name", "Сотрудник", 220),
		column("person", "Карточка", 140, "Link", "Person"),
		column("department", "Подразделение", 180),
		column("has_card", "Есть карта СКУД", 90, "Check"),
		column("absence_days", "Дней отсутствия", 90, "Int"),
		column("last_event", "Последний проход", 150, "Datetime"),
	]
