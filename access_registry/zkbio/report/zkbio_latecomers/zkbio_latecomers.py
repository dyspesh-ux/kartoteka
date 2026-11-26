# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

"""СКУД: опоздания — кто пришёл позже порога (по умолчанию 09:00).

Опоздание — первое событие дня строго позже порога. Уволенные, отсутствующие
(HR Absence) и выходные исключаются, как в «СКУД: приходы и уходы». Умолчания
фильтров (вчера, 09:00, только опоздавшие) подобраны под штатный Auto Email
Report: его фильтры статичны, поэтому пустые даты дают «отчёт за вчера».
"""

from frappe.utils import cint

from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import attendance_rows, column


def execute(filters=None):
	filters = filters or {}
	threshold = filters.get("threshold") or "09:00"
	rows = attendance_rows(filters, threshold=threshold)
	for row in rows:
		if row.late_minutes is not None and row.late_minutes <= 0:
			row.late_minutes = None  # пришёл вовремя или раньше порога
	if cint(filters.get("only_late", 1)):
		rows = [r for r in rows if r.late_minutes]
	columns = [
		column("day", "Дата", 95, "Date"),
		column("person_name", "Сотрудник", 220),
		column("person", "Карточка", 140, "Link", "Person"),
		column("department", "Подразделение", 180),
		column("first_event", "Первое событие", 110),
		column("late_minutes", "Опоздание, мин", 110, "Int"),
		column("last_event", "Последнее событие", 110),
		column("points", "Точки прохода", 180),
		column("absence", "Отсутствие", 160),
	]
	return columns, rows
