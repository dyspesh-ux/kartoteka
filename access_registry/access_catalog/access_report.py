"""Full report on access to 1C: who has access to which base, with the employee's HR context.

One row per 1C user of a base (or per profile with «По профилям»): employee, position, department
and main place of work from the HR layer (ZUP), then the access itself.
"""

import re
from collections import defaultdict

import frappe
from frappe import _

MAIN_KIND = "ОсновноеМестоРаботы"
ACTIVE = ("Работает", "Увольняется")
# kinds of employment of ZUP (code or presentation) → a short word for the report
KIND_SHORT = {
	"ОсновноеМестоРаботы": "основное",
	"Основное место работы": "основное",
	"ВнешнееСовместительство": "внешн. совм.",
	"Внешнее совместительство": "внешн. совм.",
	"ВнутреннееСовместительство": "внутр. совм.",
	"Внутреннее совместительство": "внутр. совм.",
	"Совместительство": "совм.",
	"Подработка": "подработка",
}
LEGAL_FORMS = [
	("Публичное акционерное общество", "ПАО"),
	("Непубличное акционерное общество", "АО"),
	("Закрытое акционерное общество", "ЗАО"),
	("Открытое акционерное общество", "ОАО"),
	("Акционерное общество", "АО"),
	("Общество с ограниченной ответственностью", "ООО"),
	("Индивидуальный предприниматель", "ИП"),
	("Автономная некоммерческая организация", "АНО"),
	("Некоммерческая организация", "НКО"),
	("Государственное бюджетное учреждение", "ГБУ"),
	("Федеральное государственное унитарное предприятие", "ФГУП"),
	("Муниципальное унитарное предприятие", "МУП"),
]
# modes of the «Организации» restriction of a 1C profile that give every organization
ALL_MODES = ("all", "unrestricted")


def short_org(title: str | None) -> str:
	"""«Общество с ограниченной ответственностью "Ромашка"» → «ООО «Ромашка»»."""
	if not title:
		return ""
	text = title.strip()
	for full, short in LEGAL_FORMS:
		text = re.sub(re.escape(full), short, text, flags=re.IGNORECASE)
	text = re.sub(r'"([^"]+)"', r"«\1»", text)
	return re.sub(r"\s+", " ", text)


def short_kind(kind: str | None, status: str | None = None) -> str:
	text = KIND_SHORT.get(kind or "", (kind or "").lower())
	if status and status not in ACTIVE:
		return f"{text}, {status.lower()}" if text else status.lower()
	return text


def orgs_summary(user_profiles: list, all_orgs: int, fallback: str | None) -> str:
	"""Organizations of a user with the profile that gives them.

	In 1C the organizations of an access group restrict only the rights of its own profile: one profile
	may give every organization and another only some. The summary says which is which instead of a
	bare «все» next to a list that seems to contradict it."""
	by_text = defaultdict(list)
	for p in user_profiles:
		if p.orgs_mode in ALL_MODES:
			text = "все"
		elif p.orgs_mode == "only":
			text = ", ".join(short_org(x) for x in (p.orgs_text or "").split(", ") if x) or "ни одной"
		else:
			text = p.orgs_text or ""
		if p.profile_name and p.profile_name not in by_text[text]:
			by_text[text].append(p.profile_name)
	if not by_text:
		return "все" if all_orgs else ", ".join(short_org(x) for x in (fallback or "").split(", ") if x)
	if len(by_text) == 1:
		return next(iter(by_text))
	order = sorted(by_text, key=lambda t: (t != "все", t))
	return "; ".join(f"{t} — {', '.join(by_text[t])}" for t in order)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	users = load_users(filters)
	places = main_places([u.person for u in users if u.person])
	profiles = load_profiles([u.name for u in users])

	rows = []
	for user in users:
		place = places.get(user.person) or {}
		if filters.organization and place.get("organization") != filters.organization:
			continue
		if filters.department and place.get("department") != filters.department:
			continue
		base_row = {
			"user": user.name,
			"user_name": user.user_name,
			"login": user.login,
			"ad_login": user.ad_login,
			"person": user.person,
			"employee": user.full_name or "",
			"person_status": user.person_status,
			"position": place.get("position_title"),
			"department": place.get("department"),
			"department_title": place.get("department_title"),
			"organization": place.get("organization"),
			"organization_title": short_org(place.get("organization_title")),
			"employment_kind": short_kind(
				place.get("employment_kind_code") or place.get("employment_kind"), place.get("status")
			)
			if place
			else "",
			"base_code": user.base_code,
			"base_configuration": user.base_configuration,
			"login_allowed": user.login_allowed,
			"orgs_text": orgs_summary(profiles.get(user.name, []), user.all_orgs, user.orgs_text),
			"extra_roles": (user.extra_roles or "").replace("\n", ", "),
			"link_note": user.person_link_note if not user.person else user.person_link_method,
		}
		user_profiles = profiles.get(user.name, [])
		if filters.by_profile and user_profiles:
			for p in user_profiles:
				rows.append(
					{
						**base_row,
						"profiles": p.profile_name,
						"access_group": p.access_group_name,
						"profile_orgs": p.orgs_text,
						"restrictions": (p.restrictions_text or "").replace("\n", "; "),
					}
				)
		else:
			base_row["profiles"] = ", ".join(
				sorted({p.profile_name for p in user_profiles if p.profile_name})
			)
			rows.append(base_row)
	return columns(filters), rows, MESSAGE


MESSAGE = _(
	"Организации в 1С ограничивают каждый профиль отдельно: «все — Администратор; ООО «Альфа» — Бухгалтер» "
	"значит, что с правами профиля «Администратор» видны все организации, а с правами «Бухгалтера» — "
	"только ООО «Альфа». «По профилям» покажет строку на каждый профиль."
)


def load_users(filters):
	conditions = ["u.missing_in_source = 0"]
	params = {}
	if not filters.include_disabled:
		conditions.append("u.login_allowed = 1 and u.invalid = 0")
	if filters.base_code:
		conditions.append("u.base_code = %(base_code)s")
		params["base_code"] = filters.base_code
	if filters.configuration:
		conditions.append("u.base_configuration = %(configuration)s")
		params["configuration"] = filters.configuration
	if filters.person:
		conditions.append("u.person = %(person)s")
		params["person"] = filters.person
	if filters.only_unlinked:
		conditions.append("ifnull(u.person, '') = ''")
	return frappe.db.sql(
		f"""select u.name, u.user_name, u.login, u.ad_login, u.person, u.person_link_method,
			u.person_link_note, u.base_code, u.base_configuration, u.login_allowed, u.orgs_text, u.all_orgs,
			u.extra_roles, p.full_name, p.status as person_status
		from `tabIB User` u left join `tabPerson` p on p.name = u.person
		where {" and ".join(conditions)}
		order by coalesce(p.full_name, u.user_name), u.base_code""",
		params,
		as_dict=True,
	)


def main_places(persons: list) -> dict:
	"""Main place of work of each person: the active main employment; if the person works only part-time,
	the active employment hired last. Dismissed people get their latest employment."""
	if not persons:
		return {}
	rows = frappe.db.sql(
		"""select e.person, e.status, e.employment_kind_code, e.employment_kind, e.hire_date,
			e.organization, o.title as organization_title, e.department, d.title as department_title,
			pos.title as position_title
		from `tabEmployment` e
			left join `tabHR Organization` o on o.name = e.organization
			left join `tabHR Department` d on d.name = e.department
			left join `tabHR Position` pos on pos.name = e.position
		where e.person in %(persons)s""",
		{"persons": tuple(set(persons))},
		as_dict=True,
	)
	by_person = defaultdict(list)
	for row in rows:
		by_person[row.person].append(row)
	result = {}
	for person, employments in by_person.items():
		employments.sort(
			key=lambda e: (
				e.status in ACTIVE,
				e.employment_kind_code == MAIN_KIND,
				str(e.hire_date or ""),
			),
			reverse=True,
		)
		best = employments[0]
		kind = best.employment_kind or best.employment_kind_code or ""
		if best.status not in ACTIVE:
			kind = f"{kind} (не работает: {best.status})" if kind else f"не работает: {best.status}"
		result[person] = {**best, "employment_kind": kind}
	return result


def load_profiles(users: list) -> dict:
	if not users:
		return {}
	rows = frappe.db.sql(
		"""select parent, profile_name, access_group_name, orgs_mode, orgs_text, restrictions_text
		from `tabIB User Profile` where parenttype = 'IB User' and parent in %(users)s
		order by parent, profile_name""",
		{"users": tuple(users)},
		as_dict=True,
	)
	result = defaultdict(list)
	for row in rows:
		result[row.parent].append(row)
	return result


def columns(filters):
	cols = [
		{"fieldname": "employee", "label": _("Сотрудник"), "fieldtype": "Data", "width": 220},
		{"fieldname": "user_name", "label": _("Пользователь 1С"), "fieldtype": "Data", "width": 220},
		{"fieldname": "position", "label": _("Должность"), "fieldtype": "Data", "width": 140},
		{"fieldname": "department_title", "label": _("Подразделение"), "fieldtype": "Data", "width": 140},
		{
			"fieldname": "organization_title",
			"label": _("Место работы"),
			"fieldtype": "Data",
			"width": 130,
		},
		{"fieldname": "employment_kind", "label": _("Занятость"), "fieldtype": "Data", "width": 95},
		{
			"fieldname": "base_code",
			"label": _("База"),
			"fieldtype": "Link",
			"options": "Info Base",
			"width": 90,
		},
		{"fieldname": "base_configuration", "label": _("Конфигурация"), "fieldtype": "Data", "width": 110},
		{"fieldname": "login", "label": _("Логин"), "fieldtype": "Data", "width": 140},
		{"fieldname": "ad_login", "label": _("Логин AD"), "fieldtype": "Data", "width": 110},
		{"fieldname": "login_allowed", "label": _("Вход"), "fieldtype": "Check", "width": 60},
		{"fieldname": "person_status", "label": _("Статус сотрудника"), "fieldtype": "Data", "width": 120},
	]
	if filters.by_profile:
		cols += [
			{"fieldname": "profiles", "label": _("Профиль"), "fieldtype": "Data", "width": 200},
			{"fieldname": "access_group", "label": _("Группа доступа"), "fieldtype": "Data", "width": 180},
			{
				"fieldname": "profile_orgs",
				"label": _("Организации профиля"),
				"fieldtype": "Data",
				"width": 220,
			},
			{
				"fieldname": "restrictions",
				"label": _("Ограничения доступа"),
				"fieldtype": "Data",
				"width": 320,
			},
		]
	else:
		cols += [
			{"fieldname": "profiles", "label": _("Профили"), "fieldtype": "Data", "width": 300},
			{
				"fieldname": "orgs_text",
				"label": _("Организации (по профилям)"),
				"fieldtype": "Data",
				"width": 260,
			},
		]
	cols += [
		{"fieldname": "extra_roles", "label": _("Роли в обход профилей"), "fieldtype": "Data", "width": 220},
		{"fieldname": "link_note", "label": _("Привязка к сотруднику"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "user",
			"label": _("Карточка пользователя"),
			"fieldtype": "Link",
			"options": "IB User",
			"width": 160,
		},
		{
			"fieldname": "person",
			"label": _("Карточка сотрудника"),
			"fieldtype": "Link",
			"options": "Person",
			"width": 160,
		},
	]
	return cols
