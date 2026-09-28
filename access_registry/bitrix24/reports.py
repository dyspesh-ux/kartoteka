"""Bitrix24 reports: comparison with the HR data and access to sections."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

from access_registry.access_catalog.access_report import main_places
from access_registry.bitrix24.access import effective_grants
from access_registry.sync.normalize import normalize_name

ACTIVE_B24 = "u.active = 1 and u.missing_in_source = 0 and ifnull(u.user_type, '') in ('', 'employee')"


def col(fieldname, label, fieldtype="Data", width=160, options=None):
	c = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
	if options:
		c["options"] = options
	return c


USER_COLS = [
	col("user_name", _("Пользователь Битрикс24"), width=230),
	col("portal", _("Портал"), "Link", 80, "B24 Portal"),
]
PERSON_COLS = [
	col("person", _("Сотрудник"), "Link", 230, "Person"),
	col("person_status", _("Статус"), width=110),
]
LINK_COL = col("user", _("Карточка"), "Link", 150, "B24 User")


def _portal_condition(filters, alias="u"):
	if filters.get("portal"):
		return f" and {alias}.portal = %(portal)s"
	return ""


def _date(value) -> str:
	return getdate(value).strftime("%d.%m.%Y") if value else ""


# ------------------------------------------------------------------ users


def active_not_working(filters=None):
	"""Активен в Битрикс24, а сотрудник по кадрам не работает."""
	filters = frappe._dict(filters or {})
	data = frappe.db.sql(
		f"""select u.name as user, u.full_name as user_name, u.portal, u.person, p.status as person_status,
			u.last_login, u.is_admin, u.person_link_method
		from `tabB24 User` u join `tabPerson` p on p.name = u.person
		where {ACTIVE_B24} and p.status != 'Работает' {_portal_condition(filters)}
		order by u.full_name""",
		filters,
		as_dict=True,
	)
	columns = (
		USER_COLS
		+ PERSON_COLS
		+ [
			col("last_login", _("Последний вход"), "Datetime", 150),
			col("is_admin", _("Администратор"), "Check", 100),
			col("person_link_method", _("Как найден"), width=130),
			LINK_COL,
		]
	)
	return columns, data


def without_employee(filters=None):
	"""Активные пользователи-сотрудники Битрикс24, не привязанные к сотруднику."""
	filters = frappe._dict(filters or {})
	data = frappe.db.sql(
		f"""select u.name as user, u.full_name as user_name, u.portal, u.email, u.work_position,
			u.person_link_note, u.last_login
		from `tabB24 User` u where {ACTIVE_B24} and ifnull(u.person, '') = '' {_portal_condition(filters)}
		order by u.full_name""",
		filters,
		as_dict=True,
	)
	columns = USER_COLS + [
		col("email", _("Почта"), width=200),
		col("work_position", _("Должность в Битрикс24"), width=180),
		col("person_link_note", _("Почему не найден"), width=300),
		col("last_login", _("Последний вход"), "Datetime", 150),
		LINK_COL,
	]
	return columns, data


# ------------------------------------------------------------------ profile vs HR


def _hr_to_b24_departments(portal=None) -> dict:
	result = defaultdict(set)
	filters = {"missing_in_source": 0, "hr_department": ["is", "set"]}
	if portal:
		filters["portal"] = portal
	for name, hr in frappe.get_all(
		"B24 Department", filters=filters, fields=["name", "hr_department"], as_list=True
	):
		result[hr].add(name)
	return result


def profile_differences(filters=None):
	"""Отчество, дата рождения, должность и подразделение в Битрикс24 не совпадают с кадрами."""
	filters = frappe._dict(filters or {})
	users = frappe.db.sql(
		f"""select u.name as user, u.full_name as user_name, u.portal, u.person, u.second_name, u.birthday,
			u.work_position, p.middle_name, p.birth_date, p.status as person_status
		from `tabB24 User` u join `tabPerson` p on p.name = u.person
		where {ACTIVE_B24} {_portal_condition(filters)} order by u.full_name""",
		filters,
		as_dict=True,
	)
	places = main_places([u.person for u in users])
	departments = defaultdict(list)
	for parent, dept, title in frappe.db.sql(
		"""select d.parent, d.department, d.department_name from `tabB24 User Department` d
		join `tabB24 User` u on u.name = d.parent where u.missing_in_source = 0"""
	):
		departments[parent].append((dept, title))
	expected = _hr_to_b24_departments(filters.get("portal"))
	data = []
	for u in users:
		place = places.get(u.person, {})
		diffs = []
		if u.middle_name and normalize_name(u.second_name) != normalize_name(u.middle_name):
			diffs.append((_("Отчество"), u.second_name or "", u.middle_name))
		if u.birth_date and (not u.birthday or getdate(u.birthday) != getdate(u.birth_date)):
			diffs.append((_("Дата рождения"), _date(u.birthday), _date(u.birth_date)))
		position = place.get("position_title")
		if position and normalize_name(u.work_position) != normalize_name(position):
			diffs.append((_("Должность"), u.work_position or "", position))
		hr_dept = place.get("department")
		mine = departments.get(u.user, [])
		if hr_dept and expected.get(hr_dept) and not expected[hr_dept] & {d for d, _t in mine}:
			diffs.append(
				(_("Подразделение"), ", ".join(t for _d, t in mine), place.get("department_title") or hr_dept)
			)
		for field, b24_value, hr_value in diffs:
			if filters.get("field") and filters.field != field:
				continue
			data.append({**u, "field": field, "b24_value": b24_value, "hr_value": hr_value})
	columns = USER_COLS + [
		col("person", _("Сотрудник"), "Link", 200, "Person"),
		col("field", _("Что отличается"), width=130),
		col("b24_value", _("В Битрикс24"), width=220),
		col("hr_value", _("По кадрам"), width=220),
		LINK_COL,
	]
	return columns, data


# ------------------------------------------------------------------ structure


def department_heads(filters=None):
	"""Руководители подразделений: Битрикс24 и кадры."""
	filters = frappe._dict(filters or {})
	conditions = "d.missing_in_source = 0" + _portal_condition(filters, "d")
	rows = frappe.db.sql(
		f"""select d.name as department, d.department_name, d.portal, d.hr_department, d.hr_link_note,
			d.head, hu.full_name as b24_head_name, d.head_person, h.head as hr_head,
			hp.full_name as hr_head_name, bp.full_name as b24_head_person_name, d.member_count
		from `tabB24 Department` d
			left join `tabB24 User` hu on hu.name = d.head
			left join `tabPerson` bp on bp.name = d.head_person
			left join `tabHR Department` h on h.name = d.hr_department
			left join `tabPerson` hp on hp.name = h.head
		where {conditions} order by d.department_name""",
		filters,
		as_dict=True,
	)
	data = []
	for r in rows:
		if not r.hr_department:
			r.status = _("не сопоставлено с кадрами")
		elif not r.hr_head and not r.head:
			r.status = _("не указан нигде")
		elif not r.head:
			r.status = _("в Битрикс24 не указан")
		elif not r.hr_head:
			r.status = _("в кадрах не указан")
		elif r.head_person == r.hr_head:
			r.status = _("совпадает")
		elif not r.head_person:
			r.status = _("руководитель в Битрикс24 не привязан к сотруднику")
		else:
			r.status = _("отличается")
		if cint(filters.get("only_differences")) and r.status == _("совпадает"):
			continue
		data.append(r)
	columns = [
		col("department_name", _("Подразделение Битрикс24"), width=230),
		col("portal", _("Портал"), "Link", 80, "B24 Portal"),
		col("status", _("Итог"), width=200),
		col("b24_head_name", _("Руководитель в Битрикс24"), width=210),
		col("hr_head_name", _("Руководитель по кадрам"), width=210),
		col("hr_department", _("Подразделение в кадрах"), "Link", 200, "HR Department"),
		col("hr_link_note", _("Почему не сопоставлено"), width=220),
		col("member_count", _("Сотрудников"), "Int", 90),
		col("department", _("Карточка"), "Link", 150, "B24 Department"),
	]
	return columns, data


def structure(filters=None):
	"""Структура: подразделения Битрикс24 без пары в кадрах и подразделения кадров без пары в Битрикс24."""
	filters = frappe._dict(filters or {})
	data = []
	for d in frappe.db.sql(
		f"""select d.name, d.department_name, d.portal, d.member_count, d.hr_link_note, p.department_name as parent_name
		from `tabB24 Department` d left join `tabB24 Department` p on p.name = d.parent_department
		where d.missing_in_source = 0 and ifnull(d.hr_department, '') = '' {_portal_condition(filters, "d")}
		order by d.department_name""",
		filters,
		as_dict=True,
	):
		data.append(
			{
				"side": _("Есть в Битрикс24, нет в кадрах"),
				"title": d.department_name,
				"parent": d.parent_name,
				"employees": d.member_count,
				"note": d.hr_link_note,
				"b24_department": d.name,
			}
		)
	mapped = set(_hr_to_b24_departments(filters.get("portal")))
	for h in frappe.db.sql(
		"""select h.name, h.title, p.title as parent_name, count(e.name) as employees
		from `tabHR Department` h
			join `tabEmployment` e on e.department = h.name and e.status in ('Работает', 'Увольняется')
			left join `tabHR Department` p on p.name = h.parent_hr_department
		where h.missing = 0 and h.node_type = 'Подразделение'
		group by h.name, h.title, p.title order by h.title""",
		as_dict=True,
	):
		if h.name in mapped:
			continue
		data.append(
			{
				"side": _("Есть в кадрах, нет в Битрикс24"),
				"title": h.title,
				"parent": h.parent_name,
				"employees": h.employees,
				"note": "",
				"hr_department": h.name,
			}
		)
	columns = [
		col("side", _("Расхождение"), width=230),
		col("title", _("Подразделение"), width=240),
		col("parent", _("Входит в"), width=200),
		col("employees", _("Сотрудников"), "Int", 100),
		col("note", _("Пояснение"), width=260),
		col("b24_department", _("Битрикс24"), "Link", 150, "B24 Department"),
		col("hr_department", _("Кадры"), "Link", 150, "HR Department"),
	]
	return columns, data


# ------------------------------------------------------------------ absences


def _overlap(a_from, a_to, b_from, b_to) -> bool:
	return getdate(a_from) <= getdate(b_to or b_from) and getdate(b_from) <= getdate(a_to or a_from)


def absence_differences(filters=None):
	"""Отсутствия: есть в ЗУП, но нет в графике Битрикс24, и наоборот."""
	filters = frappe._dict(filters or {})
	since = add_days(today(), -(cint(filters.get("days_back")) or 30))
	until = add_days(today(), cint(filters.get("days_ahead")) or 60)
	portal = filters.get("portal")
	linked = {
		p
		for p in frappe.db.sql_list(
			f"select distinct u.person from `tabB24 User` u where {ACTIVE_B24} and ifnull(u.person, '') != ''"
			+ (" and u.portal = %(portal)s" if portal else ""),
			filters,
		)
	}
	b24 = defaultdict(list)
	b24_filters = {
		"missing_in_source": 0,
		"date_to": [">=", since],
		"date_from": ["<=", until],
		"person": ["is", "set"],
	}
	if portal:
		b24_filters["portal"] = portal
	for a in frappe.get_all(
		"B24 Absence",
		filters=b24_filters,
		fields=["name", "person", "date_from", "date_to", "absence_type", "portal"],
	):
		b24[a.person].append(a)
	hr = defaultdict(list)
	for a in frappe.db.sql(
		"""select a.name, a.person, a.date_from, a.date_to, a.state, a.category
		from `tabHR Absence` a where a.cancelled = 0 and a.date_from <= %s
			and ifnull(a.date_to, a.date_from) >= %s""",
		(until, since),
		as_dict=True,
	):
		if a.person in linked:
			hr[a.person].append(a)
	names = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", list(linked) or [""]]},
			fields=["name", "full_name"],
			as_list=True,
		)
	)
	data = []
	for person, items in hr.items():
		for a in items:
			if not any(_overlap(a.date_from, a.date_to, b.date_from, b.date_to) for b in b24.get(person, [])):
				data.append(
					{
						"person": person,
						"full_name": names.get(person),
						"status": _("Нет в Битрикс24"),
						"date_from": a.date_from,
						"date_to": a.date_to,
						"kind": a.state or a.category,
						"hr_absence": a.name,
					}
				)
	for person, items in b24.items():
		if person not in linked:
			continue
		for b in items:
			if not any(_overlap(b.date_from, b.date_to, a.date_from, a.date_to) for a in hr.get(person, [])):
				data.append(
					{
						"person": person,
						"full_name": names.get(person),
						"status": _("Нет в ЗУП"),
						"date_from": b.date_from,
						"date_to": b.date_to,
						"kind": b.absence_type,
						"b24_absence": b.name,
					}
				)
	data.sort(key=lambda r: (r["full_name"] or "", str(r["date_from"])))
	columns = [
		col("full_name", _("Сотрудник"), width=230),
		col("status", _("Расхождение"), width=140),
		col("date_from", _("С"), "Date", 100),
		col("date_to", _("По"), "Date", 100),
		col("kind", _("Вид"), width=200),
		col("person", _("Карточка"), "Link", 150, "Person"),
		col("hr_absence", _("Отсутствие в ЗУП"), "Link", 150, "HR Absence"),
		col("b24_absence", _("Отсутствие в Битрикс24"), "Link", 150, "B24 Absence"),
	]
	return columns, data


# ------------------------------------------------------------------ access


def section_access(filters=None):
	"""Кто имеет доступ к разделам Битрикс24: CRM, смарт-процессы, общие диски, группы пользователей."""
	filters = frappe._dict(filters or {})
	portals = [filters.portal] if filters.get("portal") else frappe.get_all("B24 Portal", pluck="name")
	rows = []
	for portal in portals:
		rows += effective_grants(portal, filters)
	statuses = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", [r["person"] for r in rows if r["person"]] or [""]]},
			fields=["name", "status"],
			as_list=True,
			limit_page_length=0,
		)
	)
	data = []
	for r in rows:
		r["person_status"] = statuses.get(r["person"]) or ("" if r["person"] else _("не привязан"))
		if filters.get("person") and r["person"] != filters.person:
			continue
		if cint(filters.get("only_not_working")) and (not r["person"] or r["person_status"] == "Работает"):
			continue
		if not cint(filters.get("include_inactive")) and not r["active"]:
			continue
		data.append(r)
	columns = [
		col("resource_type", _("Раздел"), width=120),
		col("resource", _("Ресурс"), width=220),
		col("permission", _("Права"), width=260),
		col("via", _("Через"), width=190),
		col("principal", _("Кому выдано"), width=220),
		col("user_name", _("Пользователь"), width=200),
		col("person_status", _("Статус сотрудника"), width=120),
		col("person", _("Сотрудник"), "Link", 150, "Person"),
		col("user", _("Карточка"), "Link", 150, "B24 User"),
	]
	return columns, data


def workgroup_members(filters=None):
	"""Участники групп и проектов Битрикс24 со статусом сотрудника."""
	filters = frappe._dict(filters or {})
	conditions = ["g.missing_in_source = 0"]
	if filters.get("group"):
		conditions.append("g.name = %(group)s")
	if filters.get("portal"):
		conditions.append("g.portal = %(portal)s")
	data = frappe.db.sql(
		f"""select g.name as workgroup, g.group_name, g.is_project, g.under_control, m.role, m.user, u.full_name as user_name,
			u.active, u.person, p.status as person_status
		from `tabB24 Workgroup Member` m
			join `tabB24 Workgroup` g on g.name = m.parent
			join `tabB24 User` u on u.name = m.user
			left join `tabPerson` p on p.name = u.person
		where {" and ".join(conditions)} order by g.group_name, m.role, u.full_name""",
		filters,
		as_dict=True,
	)
	if cint(filters.get("only_not_working")):
		data = [r for r in data if r.person_status != "Работает"]
	columns = [
		col("group_name", _("Группа или проект"), width=230),
		col("is_project", _("Проект"), "Check", 70),
		col("under_control", _("Под контролем"), "Check", 100),
		col("role", _("Роль"), width=100),
		col("user_name", _("Пользователь"), width=210),
		col("active", _("Активен"), "Check", 80),
		col("person_status", _("Статус сотрудника"), width=120),
		col("person", _("Сотрудник"), "Link", 150, "Person"),
		col("workgroup", _("Карточка группы"), "Link", 150, "B24 Workgroup"),
	]
	return columns, data
