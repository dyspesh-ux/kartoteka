# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

"""СКУД: приходы и уходы — первое/последнее событие дня по сотруднику.

Уволенные не показываются никогда; сотрудники с кадровым отсутствием на день
(отпуск, командировка — HR Absence из ЗУП) скрыты, пока не включён фильтр
«Показывать отсутствующих».
"""

import frappe
from frappe.utils import cint, getdate

ATTENDANCE_SQL = """
select e.person, date(e.event_time) as day,
	time_format(min(e.event_time), '%%H:%%i') as first_event,
	time_format(max(e.event_time), '%%H:%%i') as last_event,
	count(*) as events_count,
	group_concat(distinct e.event_point_name order by e.event_point_name separator ', ') as points,
	(select a.state from `tabHR Absence` a
		where a.person = e.person and a.cancelled = 0
		and a.date_from <= date(e.event_time)
		and coalesce(a.date_to, a.until_expected, a.date_from) >= date(e.event_time)
		limit 1) as absence
from `tabZKBio Event` e
join `tabPerson` p on p.name = e.person
where e.person is not null and e.person != ''
	and e.event_time >= %(date_from)s and e.event_time < date_add(%(date_to)s, interval 1 day)
	and p.status != 'Уволен'
group by e.person, date(e.event_time)
order by day, e.person
"""


def column(fieldname, label, width=0, fieldtype="Data", options=None):
	col = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype}
	if width:
		col["width"] = width
	if options:
		col["options"] = options
	return col


def department_map():
	"""person → active department title (the first active employment wins)."""
	rows = frappe.get_all(
		"Employment",
		filters={"status": "Работает", "missing": 0},
		fields=["person", "department"],
		limit_page_length=0,
	)
	titles = dict(
		frappe.get_all("HR Department", fields=["name", "title"], as_list=True, limit_page_length=0)
	)
	result = {}
	for row in rows:
		if row.person not in result and row.department:
			result[row.person] = (row.department, titles.get(row.department, row.department))
	return result


def execute(filters=None):
	filters = filters or {}
	date_from, date_to = filters.get("date_from"), filters.get("date_to")
	if not date_from or not date_to:
		frappe.throw("Задайте период отчёта (даты «с» и «по»).")
	rows = frappe.db.sql(
		ATTENDANCE_SQL, {"date_from": getdate(date_from), "date_to": getdate(date_to)}, as_dict=True
	)
	departments = department_map()
	persons = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", [r.person for r in rows] or [""]]},
			fields=["name", "full_name"],
			as_list=True,
			limit_page_length=0,
		)
	)
	show_absent = cint(filters.get("show_absent"))
	department = filters.get("department") or ""
	result = []
	for row in rows:
		if row.absence and not show_absent:
			continue
		dept = departments.get(row.person)
		if department and (not dept or dept[0] != department):
			continue
		row["person_name"] = persons.get(row.person) or ""
		row["department"] = dept[1] if dept else ""
		result.append(row)
	columns = [
		column("day", "Дата", 95, "Date"),
		column("person_name", "Сотрудник", 220),
		column("person", "Карточка", 140, "Link", "Person"),
		column("department", "Подразделение", 180),
		column("first_event", "Первое событие", 110),
		column("last_event", "Последнее событие", 110),
		column("events_count", "Событий", 90, "Int"),
		column("points", "Точки прохода", 200),
		column("absence", "Отсутствие", 160),
	]
	return columns, result
