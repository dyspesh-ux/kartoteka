"""Reports of business processes: participants and continuity risks."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint

from access_registry.access_roles import engine


def col(fieldname, label, fieldtype="Data", width=160, options=None):
	c = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
	if options:
		c["options"] = options
	return c


def _participants(model):
	"""{process role: {person: participation}} — manual and through access roles."""
	result = defaultdict(dict)
	if not model.process_roles:
		return result
	for person in model.persons:
		for role, how in model.process_roles_of(person).items():
			result[role][person] = how
	return result


def participants(filters=None):
	"""Участники процессов: кто в какой роли какого процесса."""
	filters = frappe._dict(filters or {})
	model = engine.RoleModel()
	presence = dict(frappe.get_all("Person", fields=["name", "presence"], as_list=True, limit_page_length=0))
	data = []
	for role_name, members in _participants(model).items():
		role = model.process_roles[role_name]
		if filters.get("process") and role.process != filters.process:
			continue
		for person, how in members.items():
			if filters.get("person") and person != filters.person:
				continue
			info = model.persons[person]
			if (
				cint(filters.get("only_problems"))
				and info.status == "Работает"
				and presence.get(person) != "Длительное отсутствие"
			):
				continue
			data.append(
				{
					"process": role.process,
					"process_title": role.process_title,
					"process_role": role_name,
					"role_name": role.role_name,
					"raci": role.raci,
					"person": person,
					"full_name": info.full_name,
					"person_status": info.status,
					"presence": presence.get(person),
					"participation": how,
				}
			)
	data.sort(key=lambda r: (r["process_title"] or "", r["role_name"] or "", r["full_name"] or ""))
	columns = [
		col("process_title", _("Процесс"), width=220),
		col("role_name", _("Роль в процессе"), width=200),
		col("raci", _("Участие"), width=150),
		col("full_name", _("Сотрудник"), width=220),
		col("participation", _("Как назначен"), width=200),
		col("person_status", _("Статус"), width=100),
		col("presence", _("Присутствие"), width=150),
		col("process_role", _("Карточка роли"), "Link", 120, "Process Role"),
		col("person", _("Карточка сотрудника"), "Link", 150, "Person"),
	]
	return columns, data


def continuity(filters=None, model=None):
	"""Риски процессов: роли без участников, без заместителя, с неработающими участниками.

	model: RoleModel already built by the caller (the dashboard)."""
	filters = frappe._dict(filters or {})
	model = model or engine.RoleModel()
	members = _participants(model)
	presence = dict(frappe.get_all("Person", fields=["name", "presence"], as_list=True, limit_page_length=0))
	settings = {
		r.name: r
		for r in frappe.get_all(
			"Process Role", fields=["name", "min_participants", "needs_deputy"], limit_page_length=0
		)
	}
	data = []
	for name, role in model.process_roles.items():
		if filters.get("process") and role.process != filters.process:
			continue
		people = members.get(name, {})
		working = [p for p in people if model.persons[p].status == "Работает"]
		available = [p for p in working if presence.get(p) != "Длительное отсутствие"]
		deputies = [p for p, how in people.items() if how == "Заместитель" and p in available]
		need = cint(settings[name].min_participants) or 1
		problems = []
		if not people:
			problems.append(_("нет участников"))
		elif len(available) < need:
			problems.append(_("доступно участников {0} из {1}").format(len(available), need))
		if cint(settings[name].needs_deputy) and not deputies and len(available) < 2:
			problems.append(_("нет заместителя"))
		gone = [model.persons[p].full_name for p in people if p not in working]
		if gone:
			problems.append(_("не работают: {0}").format(", ".join(gone)))
		if not problems and cint(filters.get("only_problems", 1)):
			continue
		data.append(
			{
				"process": role.process,
				"process_title": role.process_title,
				"process_role": name,
				"role_name": role.role_name,
				"raci": role.raci,
				"participants": len(people),
				"available": len(available),
				"problems": "; ".join(problems) or _("в порядке"),
			}
		)
	data.sort(key=lambda r: (r["process_title"] or "", r["role_name"] or ""))
	columns = [
		col("process_title", _("Процесс"), width=240),
		col("role_name", _("Роль"), width=220),
		col("raci", _("Участие"), width=150),
		col("participants", _("Участников"), "Int", 100),
		col("available", _("Доступно"), "Int", 90),
		col("problems", _("Риски"), width=360),
		col("process_role", _("Карточка роли"), "Link", 120, "Process Role"),
	]
	return columns, data
