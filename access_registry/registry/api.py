"""API of the registry app for end users (/registry): security, internal audit, rights controllers.

Every method checks the registry roles itself (see permissions.py). Personal data (birth dates)
goes only to administrators and auditors.
"""

from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, today

from access_registry.access_catalog.access_report import main_places
from access_registry.access_roles import engine
from access_registry.permissions import (
	ADMINS,
	AUDITOR,
	PERSONAL_DATA,
	PROCESS_MANAGER,
	READERS,
	ROLE_MANAGER,
	has_any,
	require,
)

LIST_LIMIT = 500
CACHE_KEY = "access_registry:registry_dashboard"
STALE_DAYS = 90


def _check():
	require(*READERS)


def _personal() -> bool:
	return has_any(*PERSONAL_DATA)


def _column(key, label, kind="text", link=None):
	return {"key": key, "label": label, "type": kind, "link": link}


# --------------------------------------------------------------------------- basics


@frappe.whitelist()
def bootstrap() -> dict:
	_check()
	user = frappe.get_cached_doc("User", frappe.session.user)
	return {
		"user": {"name": user.name, "full_name": user.full_name or user.name, "image": user.user_image},
		"can": {
			"personal": _personal(),
			"roles": has_any(*ADMINS, ROLE_MANAGER),
			"processes": has_any(*ADMINS, PROCESS_MANAGER),
			"admin": has_any(*ADMINS),
			"audit": has_any(*ADMINS, AUDITOR),
		},
		"layers": {
			"hr": frappe.db.count("Info Base", {"configuration": "ЗУП"}),
			"bases": frappe.db.count("Info Base", {"itaccess_enabled": 1}),
			"ad": frappe.db.count("AD Domain"),
			"b24": frappe.db.count("B24 Portal"),
			"roles": frappe.db.count("Access Role", {"status": "Действует"}),
			"processes": frappe.db.count("Business Process"),
		},
	}


class Accounts:
	"""Accounts of every person in 1C, AD and Bitrix24 (counts and active flags)."""

	def __init__(self):
		self.ib = defaultdict(lambda: {"total": 0, "active": 0, "extra": 0})
		for person, total, active, extra in frappe.db.sql(
			"""select person, count(*), sum(login_allowed = 1 and invalid = 0 and missing_in_source = 0),
				sum(has_extra_roles = 1 and login_allowed = 1 and invalid = 0 and missing_in_source = 0)
			from `tabIB User` where ifnull(person, '') != '' group by person"""
		):
			self.ib[person] = {"total": total, "active": int(active or 0), "extra": int(extra or 0)}
		self.ad = defaultdict(lambda: {"total": 0, "active": 0})
		for person, total, active in frappe.db.sql(
			"""select person, count(*), sum(enabled = 1 and missing_in_source = 0)
			from `tabAD Account` where ifnull(person, '') != '' group by person"""
		):
			self.ad[person] = {"total": total, "active": int(active or 0)}
		self.b24 = defaultdict(lambda: {"total": 0, "active": 0, "admin": 0})
		for person, total, active, admin in frappe.db.sql(
			"""select person, count(*), sum(active = 1 and missing_in_source = 0),
				sum(is_admin = 1 and active = 1 and missing_in_source = 0)
			from `tabB24 User` where ifnull(person, '') != '' group by person"""
		):
			self.b24[person] = {"total": total, "active": int(active or 0), "admin": int(admin or 0)}

	def active(self, person) -> dict:
		return {
			"1С": self.ib[person]["active"] if person in self.ib else 0,
			"AD": self.ad[person]["active"] if person in self.ad else 0,
			"Битрикс24": self.b24[person]["active"] if person in self.b24 else 0,
		}

	def has_active(self, person) -> bool:
		return any(self.active(person).values())


# --------------------------------------------------------------------------- dashboard


@frappe.whitelist()
def dashboard(refresh: int = 0) -> dict:
	_check()
	if not cint(refresh):
		cached = frappe.cache().get_value(CACHE_KEY)
		if cached:
			return cached
	data = _dashboard()
	frappe.cache().set_value(CACHE_KEY, data, expires_in_sec=300)
	return data


def _dashboard() -> dict:
	persons = frappe.get_all("Person", fields=["name", "status"], limit_page_length=0)
	accounts = Accounts()
	working = [p.name for p in persons if p.status == "Работает"]
	dismissed = [p.name for p in persons if p.status != "Работает" and accounts.has_active(p.name)]
	by_system = Counter()
	for person in dismissed:
		for system, count in accounts.active(person).items():
			if count:
				by_system[system] += 1

	unlinked = {
		"1С": frappe.db.sql(
			"""select count(*) from `tabIB User` where login_allowed = 1 and invalid = 0 and missing_in_source = 0
			and ifnull(person, '') = '' and service = 0 and is_orphan = 0"""
		)[0][0],
		"AD": frappe.db.sql(
			"select count(*) from `tabAD Account` where enabled = 1 and missing_in_source = 0 and ifnull(person, '') = ''"
		)[0][0],
		"Битрикс24": frappe.db.sql(
			"""select count(*) from `tabB24 User` where active = 1 and missing_in_source = 0
			and ifnull(person, '') = '' and ifnull(user_type, '') in ('', 'employee')"""
		)[0][0],
	}

	model = engine.RoleModel()
	has_model = bool(model.roles or model.process_roles)
	statuses = Counter()
	privileged = 0
	if has_model or frappe.db.count("Entitlement"):
		for row in engine.reconcile(model=model):
			statuses[row["status"]] += 1
			if row["privileged"] and row["status"] != engine.MISSING:
				privileged += 1
	sod = len(engine.sod_conflicts()) if frappe.db.count("SoD Rule", {"active": 1}) else 0

	from access_registry.business_processes.reports import continuity

	process_risks = len(continuity({"only_problems": 1})[1]) if model.process_roles else 0
	expiring = frappe.db.count(
		"Access Exception", {"valid_to": ["between", [today(), add_days(today(), 14)]]}
	) + frappe.db.count("Access Role Assignment", {"valid_to": ["between", [today(), add_days(today(), 14)]]})
	return {
		"generated": str(now_datetime()),
		"people": {"working": len(working), "total": len(persons)},
		"dismissed_access": {"people": len(dismissed), "by_system": dict(by_system)},
		"unlinked": unlinked,
		"reconciliation": {
			"enabled": has_model,
			"missing": statuses[engine.MISSING],
			"excess": statuses[engine.EXCESS],
			"excess_not_working": statuses[engine.EXCESS_NOT_WORKING],
			"exceptions": statuses[engine.EXCEPTION],
			"ok": statuses[engine.OK],
			"privileged": privileged,
		},
		"sod": sod,
		"processes": {
			"total": frappe.db.count("Business Process", {"status": "Действует"}),
			"roles": len(model.process_roles),
			"risks": process_risks,
		},
		"roles": {
			"active": len(model.roles),
			"drafts": frappe.db.count("Access Role", {"status": "Черновик"}),
			"entitlements": frappe.db.count("Entitlement", {"active": 1}),
		},
		"expiring": expiring,
		"quality": {
			"merge_candidates": frappe.db.count("Person Merge Candidate", {"status": "Открыт"}),
			"b24_admins": frappe.db.count("B24 User", {"is_admin": 1, "active": 1, "missing_in_source": 0}),
			"extra_roles": frappe.db.count(
				"IB User", {"has_extra_roles": 1, "login_allowed": 1, "invalid": 0, "missing_in_source": 0}
			),
		},
		"sources": sources(),
	}


def sources() -> list:
	result = []
	for b in frappe.get_all(
		"Info Base",
		fields=[
			"name",
			"title",
			"configuration",
			"enabled",
			"last_sync",
			"last_status",
			"itaccess_enabled",
			"itaccess_last_snapshot",
			"itaccess_last_status",
		],
		order_by="name",
	):
		if b.configuration == "ЗУП":
			result.append(
				{
					"kind": "Кадры ЗУП",
					"name": b.name,
					"title": b.title,
					"enabled": b.enabled,
					"last": b.last_sync,
					"status": b.last_status,
					"doctype": "Info Base",
				}
			)
		if b.itaccess_enabled:
			result.append(
				{
					"kind": f"Права 1С ({b.configuration})",
					"name": b.name,
					"title": b.title,
					"enabled": b.enabled,
					"last": b.itaccess_last_snapshot,
					"status": b.itaccess_last_status,
					"doctype": "Info Base",
				}
			)
	for d in frappe.get_all("AD Domain", fields=["name", "title", "enabled", "last_sync", "last_status"]):
		result.append(
			{
				"kind": "Active Directory",
				"name": d.name,
				"title": d.title,
				"enabled": d.enabled,
				"last": d.last_sync,
				"status": d.last_status,
				"doctype": "AD Domain",
			}
		)
	for p in frappe.get_all("B24 Portal", fields=["name", "title", "enabled", "last_sync", "last_status"]):
		result.append(
			{
				"kind": "Битрикс24",
				"name": p.name,
				"title": p.title,
				"enabled": p.enabled,
				"last": p.last_sync,
				"status": p.last_status,
				"doctype": "B24 Portal",
			}
		)
	for row in result:
		status = row["status"] or ""
		row["state"] = (
			"ok"
			if status.startswith("Успех")
			else "warn"
			if status.startswith("Остановлен")
			else "error"
			if status.startswith("Ошибка")
			else "none"
		)
		row["last"] = str(row["last"]) if row["last"] else None
	return result


# --------------------------------------------------------------------------- people


@frappe.whitelist()
def people(
	query: str | None = None,
	organization: str | None = None,
	status: str | None = None,
	flag: str | None = None,
	start: int = 0,
	limit: int = 100,
) -> dict:
	_check()
	filters = {}
	if status:
		filters["status"] = status
	persons = frappe.get_all(
		"Person",
		filters=filters,
		fields=["name", "full_name", "status", "presence"],
		order_by="full_name",
		limit_page_length=0,
	)
	if query:
		needle = query.strip().lower()
		persons = [p for p in persons if needle in (p.full_name or "").lower()]
	accounts = Accounts()
	places = main_places([p.name for p in persons])
	rows = []
	for p in persons:
		place = places.get(p.name, {})
		if organization and place.get("organization") != organization:
			continue
		active = accounts.active(p.name)
		flags = []
		if p.status != "Работает" and any(active.values()):
			flags.append("dismissed_access")
		if p.status == "Работает" and not any(active.values()):
			flags.append("no_access")
		if accounts.ib[p.name]["extra"] if p.name in accounts.ib else 0:
			flags.append("extra_roles")
		if accounts.b24[p.name]["admin"] if p.name in accounts.b24 else 0:
			flags.append("b24_admin")
		if flag and flag not in flags:
			continue
		rows.append(
			{
				"name": p.name,
				"full_name": p.full_name,
				"status": p.status,
				"presence": p.presence,
				"position": place.get("position_title"),
				"department": place.get("department_title"),
				"organization": place.get("organization_title"),
				"ib": active["1С"],
				"ad": active["AD"],
				"b24": active["Битрикс24"],
				"flags": flags,
			}
		)
	start, limit = cint(start), min(cint(limit) or 100, LIST_LIMIT)
	return {"total": len(rows), "rows": rows[start : start + limit]}


@frappe.whitelist()
def organizations() -> list:
	_check()
	return frappe.get_all(
		"HR Organization", filters={"missing": 0}, fields=["name", "title"], order_by="title"
	)


@frappe.whitelist()
def person(name: str) -> dict:
	_check()
	from access_registry.access_registry.page.access_overview.access_overview import _accounts, _ad_accounts

	doc = frappe.get_doc("Person", name)
	personal = _personal()
	place = main_places([name]).get(name, {})
	employments = frappe.db.sql(
		"""select e.name, e.status, e.employment_kind, e.hire_date, e.termination_date, e.tab_number, e.source,
			o.title as organization, d.title as department, pos.title as position
		from `tabEmployment` e
			left join `tabHR Organization` o on o.name = e.organization
			left join `tabHR Department` d on d.name = e.department
			left join `tabHR Position` pos on pos.name = e.position
		where e.person = %s order by e.status in ('Работает', 'Увольняется') desc, e.hire_date desc""",
		name,
		as_dict=True,
	)
	events = frappe.get_all(
		"HR Event",
		filters={"person": name},
		fields=["event_type", "event_date", "details"],
		order_by="event_date desc",
		limit=10,
	)
	absences = frappe.get_all(
		"HR Absence",
		filters={"person": name, "cancelled": 0, "date_to": [">=", add_days(today(), -30)]},
		fields=["state", "category", "date_from", "date_to"],
		order_by="date_from",
	)
	b24_users = frappe.get_all(
		"B24 User",
		filters={"person": name},
		fields=[
			"name",
			"portal",
			"full_name",
			"email",
			"active",
			"is_admin",
			"user_type",
			"work_position",
			"last_login",
			"second_name",
			"missing_in_source",
			"person_link_method",
		]
		+ (["birthday"] if personal else []),
	)
	b24_access = []
	if b24_users:
		from access_registry.bitrix24.access import effective_grants

		for portal in {u.portal for u in b24_users}:
			for row in effective_grants(portal):
				if row["person"] == name:
					b24_access.append(
						{k: row[k] for k in ("resource_type", "resource", "permission", "via", "user_name")}
					)
		groups = frappe.db.sql(
			"""select g.name, g.group_name, g.is_project, m.role, m.parent
			from `tabB24 Workgroup Member` m join `tabB24 Workgroup` g on g.name = m.parent
			join `tabB24 User` u on u.name = m.user where u.person = %s and g.missing_in_source = 0
			order by g.group_name""",
			name,
			as_dict=True,
		)
		for u in b24_users:
			u.departments = frappe.get_all(
				"B24 User Department",
				filters={"parent": u.name, "parenttype": "B24 User"},
				pluck="department_name",
			)
			u.workgroups = [g for g in groups]
			u.last_login = str(u.last_login) if u.last_login else None
	model = engine.RoleModel()
	roles = [{"role": r, "reason": reason} for r, reason in model.roles_of(name).items()]
	process_roles = []
	for pr_name, how in model.process_roles_of(name).items():
		pr = model.process_roles[pr_name]
		process_roles.append(
			{
				"process": pr.process,
				"process_title": pr.process_title,
				"role": pr.role_name,
				"raci": pr.raci,
				"how": how,
			}
		)
	return {
		"person": {
			"name": doc.name,
			"full_name": doc.full_name,
			"status": doc.status,
			"presence": doc.presence,
			"external_part_time_only": doc.external_part_time_only,
			"birth_date": str(doc.birth_date) if personal and doc.birth_date else None,
			"position": place.get("position_title"),
			"department": place.get("department_title"),
			"organization": place.get("organization_title"),
		},
		"employments": employments,
		"events": events,
		"absences": absences,
		"ib": _accounts({"person": name, "missing_in_source": 0}),
		"ad": _ad_accounts({"person": name}),
		"b24": b24_users,
		"b24_access": b24_access,
		"roles": roles,
		"process_roles": process_roles,
		"reconciliation": engine.reconcile({name}, model),
		"sod": engine.sod_conflicts({name}),
	}


# --------------------------------------------------------------------------- catalog, roles, processes


@frappe.whitelist()
def entitlements(system: str | None = None) -> list:
	_check()
	filters = {"system": system} if system else {}
	rows = frappe.get_all(
		"Entitlement",
		filters=filters,
		fields=[
			"name",
			"title",
			"system",
			"risk",
			"privileged",
			"active",
			"holders",
			"owner_person",
			"description",
		],
		order_by="system, title",
		limit_page_length=0,
	)
	in_roles = Counter(
		r.entitlement
		for r in frappe.get_all(
			"Access Role Entitlement",
			filters={"parenttype": "Access Role"},
			fields=["entitlement"],
			limit_page_length=0,
		)
	)
	owners = _names([r.owner_person for r in rows])
	for r in rows:
		r.roles = in_roles.get(r.name, 0)
		r.owner_name = owners.get(r.owner_person)
	return rows


@frappe.whitelist()
def entitlement(name: str) -> dict:
	_check()
	doc = frappe.get_doc("Entitlement", name)
	actual, _other = engine.actual_entitlements()
	holders = [p for p, held in actual.items() if name in held]
	names = frappe.get_all(
		"Person", filters={"name": ["in", holders or [""]]}, fields=["name", "full_name", "status"]
	)
	evidence = {p: actual[p][name] for p in holders}
	return {
		"doc": {
			k: doc.get(k)
			for k in (
				"name",
				"title",
				"system",
				"risk",
				"privileged",
				"active",
				"description",
				"ib_profile",
				"ad_group",
				"b24_workgroup",
				"b24_via",
				"b24_resource",
				"other_reference",
			)
		},
		"owner": _names([doc.owner_person]).get(doc.owner_person),
		"holders": sorted(
			[
				{
					"person": n.name,
					"full_name": n.full_name,
					"status": n.status,
					"evidence": evidence.get(n.name),
				}
				for n in names
			],
			key=lambda r: r["full_name"] or "",
		),
		"roles": frappe.get_all(
			"Access Role Entitlement",
			filters={"entitlement": name, "parenttype": "Access Role"},
			fields=["parent as role", "requirement"],
		),
		"process_roles": frappe.db.sql(
			"""select r.name, r.role_name, p.title as process_title, e.requirement
			from `tabProcess Role Entitlement` e join `tabProcess Role` r on r.name = e.parent
			join `tabBusiness Process` p on p.name = r.business_process where e.entitlement = %s""",
			name,
			as_dict=True,
		),
		"exceptions": frappe.get_all(
			"Access Exception", filters={"entitlement": name}, fields=["name", "person", "valid_to", "reason"]
		),
	}


@frappe.whitelist()
def roles() -> list:
	_check()
	model = engine.RoleModel()
	members = Counter()
	for person in model.persons:
		members.update(model.roles_of(person).keys())
	rows = frappe.get_all(
		"Access Role",
		fields=["name", "role_name", "kind", "status", "owner_person", "description"],
		order_by="status, role_name",
		limit_page_length=0,
	)
	counts = Counter(
		r.parent
		for r in frappe.get_all(
			"Access Role Entitlement",
			filters={"parenttype": "Access Role"},
			fields=["parent"],
			limit_page_length=0,
		)
	)
	owners = _names([r.owner_person for r in rows])
	for r in rows:
		r.members = members.get(r.name, 0)
		r.entitlements = counts.get(r.name, 0)
		r.owner_name = owners.get(r.owner_person)
	return rows


@frappe.whitelist()
def role(name: str) -> dict:
	_check()
	doc = frappe.get_doc("Access Role", name)
	model = engine.RoleModel()
	members = model.members_of_role(name) if doc.status == "Действует" else {}
	rows = engine.reconcile(set(members), model) if members else []
	titles = {e.entitlement for e in doc.entitlements}
	summary = defaultdict(Counter)
	for row in rows:
		if row["entitlement"] in titles:
			summary[row["person"]][row["status"]] += 1
	places = main_places(list(members))
	return {
		"doc": {
			"name": doc.name,
			"kind": doc.kind,
			"status": doc.status,
			"description": doc.description,
			"owner": _names([doc.owner_person]).get(doc.owner_person),
			"rules": [
				{
					"position_title": r.position_title,
					"department": frappe.db.get_value("HR Department", r.department, "title")
					if r.department
					else None,
					"include_subdepartments": r.include_subdepartments,
					"organization": frappe.db.get_value("HR Organization", r.organization, "title")
					if r.organization
					else None,
					"main_only": r.main_only,
				}
				for r in doc.rules
			],
			"entitlements": [
				{
					"entitlement": e.entitlement,
					"title": frappe.db.get_value("Entitlement", e.entitlement, "title"),
					"system": e.system,
					"requirement": e.requirement,
				}
				for e in doc.entitlements
			],
		},
		"members": sorted(
			[
				{
					"person": p,
					"full_name": model.persons[p].full_name,
					"reason": reason,
					"position": places.get(p, {}).get("position_title"),
					"department": places.get(p, {}).get("department_title"),
					"missing": summary[p][engine.MISSING],
					"ok": summary[p][engine.OK],
				}
				for p, reason in members.items()
			],
			key=lambda r: r["full_name"] or "",
		),
	}


@frappe.whitelist()
def processes() -> list:
	_check()
	from access_registry.business_processes.reports import continuity

	risks = Counter(r["process"] for r in continuity({"only_problems": 1})[1])
	role_counts = Counter(
		r.business_process
		for r in frappe.get_all("Process Role", fields=["business_process"], limit_page_length=0)
	)
	rows = frappe.get_all(
		"Business Process",
		fields=[
			"name",
			"title",
			"process_code",
			"level",
			"status",
			"owner_person",
			"parent_business_process",
			"lft",
			"version",
		],
		order_by="lft",
		limit_page_length=0,
	)
	owners = _names([r.owner_person for r in rows])
	for r in rows:
		r.roles = role_counts.get(r.name, 0)
		r.risks = risks.get(r.name, 0)
		r.owner_name = owners.get(r.owner_person)
	return rows


@frappe.whitelist()
def process(name: str) -> dict:
	_check()
	from access_registry.business_processes.reports import continuity, participants

	doc = frappe.get_doc("Business Process", name)
	problems = {
		r["process_role"]: r["problems"] for r in continuity({"process": name, "only_problems": 0})[1]
	}
	people = defaultdict(list)
	for row in participants({"process": name})[1]:
		people[row["process_role"]].append(row)
	roles_ = []
	for pr in frappe.get_all(
		"Process Role",
		filters={"business_process": name},
		fields=[
			"name",
			"role_name",
			"raci",
			"description",
			"filled_by_access_role",
			"min_participants",
			"needs_deputy",
		],
		order_by="raci, role_name",
	):
		pr.participants = people.get(pr.name, [])
		pr.problems = problems.get(pr.name, "")
		pr.entitlements = frappe.get_all(
			"Process Role Entitlement",
			filters={"parent": pr.name, "parenttype": "Process Role"},
			fields=["entitlement", "requirement"],
		)
		for e in pr.entitlements:
			e.title = frappe.db.get_value("Entitlement", e.entitlement, "title")
		roles_.append(pr)
	return {
		"doc": {
			k: doc.get(k)
			for k in (
				"name",
				"title",
				"process_code",
				"level",
				"status",
				"version",
				"effective_from",
				"regulation_url",
				"diagram",
				"goal",
				"trigger_event",
				"systems",
				"description",
			)
		},
		"owner": _names([doc.owner_person]).get(doc.owner_person),
		"parent": frappe.db.get_value("Business Process", doc.parent_business_process, "title")
		if doc.parent_business_process
		else None,
		"children": frappe.get_all(
			"Business Process", filters={"parent_business_process": name}, fields=["name", "title", "status"]
		),
		"roles": roles_,
	}


def _names(persons) -> dict:
	persons = [p for p in persons if p]
	if not persons:
		return {}
	return dict(
		frappe.get_all(
			"Person", filters={"name": ["in", persons]}, fields=["name", "full_name"], as_list=True
		)
	)


# --------------------------------------------------------------------------- control


CONTROLS = {
	"dismissed": "Доступ у неработающих",
	"unlinked": "Учётки без сотрудника",
	"excess": "Лишние доступы",
	"missing": "Не хватает доступов",
	"sod": "Конфликты полномочий",
	"privileged": "Привилегированный доступ",
	"exceptions": "Исключения и временные роли",
	"stale": "Давно не входили",
	"processes": "Риски процессов",
	"quality": "Расхождения данных",
}


@frappe.whitelist()
def control(kind: str) -> dict:
	_check()
	if kind not in CONTROLS:
		frappe.throw(_("Неизвестный раздел контроля"))
	columns, rows = globals()[f"_control_{kind}"]()
	return {
		"kind": kind,
		"title": CONTROLS[kind],
		"columns": columns,
		"rows": rows[:5000],
		"total": len(rows),
	}


def _control_dismissed():
	rows = frappe.db.sql(
		"""select p.name as person, p.full_name, p.status, '1С' as system, concat(u.base_code, ': ', ifnull(u.login, u.user_name)) as account,
			u.name as ref, 'IB User' as ref_doctype, null as last_activity
		from `tabIB User` u join `tabPerson` p on p.name = u.person
		where p.status != 'Работает' and u.login_allowed = 1 and u.invalid = 0 and u.missing_in_source = 0
		union all
		select p.name, p.full_name, p.status, 'AD', concat(a.domain, '\\\\', a.sam_account_name), a.name, 'AD Account', a.last_logon
		from `tabAD Account` a join `tabPerson` p on p.name = a.person
		where p.status != 'Работает' and a.enabled = 1 and a.missing_in_source = 0
		union all
		select p.name, p.full_name, p.status, 'Битрикс24', concat(b.portal, ': ', ifnull(b.email, b.full_name)), b.name, 'B24 User', b.last_login
		from `tabB24 User` b join `tabPerson` p on p.name = b.person
		where p.status != 'Работает' and b.active = 1 and b.missing_in_source = 0
		order by full_name""",
		as_dict=True,
	)
	for r in rows:
		r.last_activity = str(r.last_activity) if r.last_activity else None
	return [
		_column("full_name", _("Сотрудник"), "person"),
		_column("status", _("Статус"), "status"),
		_column("system", _("Система"), "badge"),
		_column("account", _("Учётная запись"), "ref"),
		_column("last_activity", _("Последний вход"), "datetime"),
	], rows


def _control_unlinked():
	rows = frappe.db.sql(
		"""select '1С' as system, concat(base_code, ': ', user_name) as account, ifnull(login, '') as login,
			person_link_note as note, name as ref, 'IB User' as ref_doctype
		from `tabIB User` where login_allowed = 1 and invalid = 0 and missing_in_source = 0
			and ifnull(person, '') = '' and service = 0 and is_orphan = 0
		union all
		select 'AD', concat(domain, ': ', display_name), sam_account_name, person_link_note, name, 'AD Account'
		from `tabAD Account` where enabled = 1 and missing_in_source = 0 and ifnull(person, '') = ''
		union all
		select 'Битрикс24', concat(portal, ': ', full_name), ifnull(email, ''), person_link_note, name, 'B24 User'
		from `tabB24 User` where active = 1 and missing_in_source = 0 and ifnull(person, '') = ''
			and ifnull(user_type, '') in ('', 'employee')
		order by system, account""",
		as_dict=True,
	)
	return [
		_column("system", _("Система"), "badge"),
		_column("account", _("Учётная запись"), "ref"),
		_column("login", _("Логин")),
		_column("note", _("Почему не найден сотрудник")),
	], rows


def _reconciliation_rows(statuses):
	return [r for r in engine.reconcile() if r["status"] in statuses]


RECON_COLUMNS = [
	_column("full_name", _("Сотрудник"), "person"),
	_column("person_status", _("Статус"), "status"),
	_column("title", _("Право доступа"), "entitlement"),
	_column("system", _("Система"), "badge"),
	_column("risk", _("Риск"), "risk"),
	_column("status", _("Итог"), "recon"),
]


def _control_excess():
	return RECON_COLUMNS + [_column("evidence", _("Где есть"))], _reconciliation_rows(
		{engine.EXCESS, engine.EXCESS_NOT_WORKING}
	)


def _control_missing():
	return RECON_COLUMNS + [_column("expected_by", _("Положено по"))], _reconciliation_rows({engine.MISSING})


def _control_sod():
	return [
		_column("full_name", _("Сотрудник"), "person"),
		_column("person_status", _("Статус"), "status"),
		_column("title", _("Конфликт")),
		_column("severity", _("Критичность"), "risk"),
		_column("side_a", _("Первая сторона")),
		_column("side_b", _("Вторая сторона")),
	], engine.sod_conflicts()


def _control_privileged():
	rows = [
		{**r, "source": r["title"]}
		for r in engine.reconcile()
		if (r["privileged"] or r["risk"] in ("Высокий", "Критичный")) and r["status"] != engine.MISSING
	]
	for u in frappe.db.sql(
		"""select u.person, p.full_name, p.status as person_status, u.base_code, u.extra_roles
		from `tabIB User` u left join `tabPerson` p on p.name = u.person
		where u.has_extra_roles = 1 and u.login_allowed = 1 and u.invalid = 0 and u.missing_in_source = 0""",
		as_dict=True,
	):
		rows.append(
			{
				"person": u.person,
				"full_name": u.full_name or _("(не привязан)"),
				"person_status": u.person_status,
				"title": _("1С {0}: роли в обход профилей").format(u.base_code),
				"system": "1С",
				"risk": "Высокий",
				"status": (u.extra_roles or "").replace("\n", ", "),
			}
		)
	for b in frappe.db.sql(
		"""select b.person, p.full_name, p.status as person_status, b.portal, b.full_name as user_name
		from `tabB24 User` b left join `tabPerson` p on p.name = b.person
		where b.is_admin = 1 and b.active = 1 and b.missing_in_source = 0""",
		as_dict=True,
	):
		rows.append(
			{
				"person": b.person,
				"full_name": b.full_name or b.user_name,
				"person_status": b.person_status,
				"title": _("Битрикс24 {0}: администратор портала").format(b.portal),
				"system": "Битрикс24",
				"risk": "Критичный",
				"status": "",
			}
		)
	rows.sort(key=lambda r: ({"Критичный": 0, "Высокий": 1}.get(r["risk"], 2), r["full_name"] or ""))
	return [
		_column("full_name", _("Сотрудник"), "person"),
		_column("person_status", _("Статус"), "status"),
		_column("title", _("Доступ")),
		_column("system", _("Система"), "badge"),
		_column("risk", _("Риск"), "risk"),
		_column("status", _("Подробности")),
	], rows


def _control_exceptions():
	rows = []
	for e in frappe.db.sql(
		"""select e.person, p.full_name, p.status as person_status, en.title, e.valid_to, e.reason, e.approved_by
		from `tabAccess Exception` e join `tabPerson` p on p.name = e.person
		join `tabEntitlement` en on en.name = e.entitlement order by e.valid_to is null, e.valid_to""",
		as_dict=True,
	):
		rows.append({**e, "kind": _("Исключение"), "valid_to": str(e.valid_to) if e.valid_to else None})
	for a in frappe.db.sql(
		"""select a.person, p.full_name, p.status as person_status, a.access_role as title, a.valid_to, a.reason, a.approved_by
		from `tabAccess Role Assignment` a join `tabPerson` p on p.name = a.person order by a.valid_to is null, a.valid_to""",
		as_dict=True,
	):
		rows.append({**a, "kind": _("Роль вручную"), "valid_to": str(a.valid_to) if a.valid_to else None})
	return [
		_column("full_name", _("Сотрудник"), "person"),
		_column("kind", _("Вид"), "badge"),
		_column("title", _("Что")),
		_column("valid_to", _("До"), "deadline"),
		_column("reason", _("Основание")),
		_column("approved_by", _("Согласовал")),
	], rows


def _control_stale():
	since = add_days(now_datetime(), -STALE_DAYS)
	rows = frappe.db.sql(
		"""select a.person, p.full_name, p.status as person_status, 'AD' as system,
			concat(a.domain, '\\\\', a.sam_account_name) as account, a.last_logon as last_activity, a.name as ref, 'AD Account' as ref_doctype
		from `tabAD Account` a left join `tabPerson` p on p.name = a.person
		where a.enabled = 1 and a.missing_in_source = 0 and (a.last_logon is null or a.last_logon < %(since)s)
		union all
		select b.person, p.full_name, p.status, 'Битрикс24', concat(b.portal, ': ', b.full_name), b.last_login, b.name, 'B24 User'
		from `tabB24 User` b left join `tabPerson` p on p.name = b.person
		where b.active = 1 and b.missing_in_source = 0 and ifnull(b.user_type, '') in ('', 'employee')
			and (b.last_login is null or b.last_login < %(since)s)
		order by last_activity""",
		{"since": since},
		as_dict=True,
	)
	for r in rows:
		r.last_activity = str(r.last_activity) if r.last_activity else None
		r.full_name = r.full_name or _("(не привязан)")
	return [
		_column("full_name", _("Сотрудник"), "person"),
		_column("person_status", _("Статус"), "status"),
		_column("system", _("Система"), "badge"),
		_column("account", _("Учётная запись"), "ref"),
		_column("last_activity", _("Последний вход"), "datetime"),
	], rows


def _control_processes():
	from access_registry.business_processes.reports import continuity

	return [
		_column("process_title", _("Процесс"), "process"),
		_column("role_name", _("Роль")),
		_column("raci", _("Участие")),
		_column("available", _("Доступно участников"), "number"),
		_column("problems", _("Риски"), "problems"),
	], continuity({"only_problems": 1})[1]


def _control_quality():
	rows = []
	if frappe.db.count("B24 User"):
		from access_registry.bitrix24 import reports as b24

		for r in b24.profile_differences({})[1]:
			if r["field"] == "Дата рождения" and not _personal():
				r["b24_value"] = r["hr_value"] = "•••"
			rows.append(
				{
					"person": r["person"],
					"full_name": r["user_name"],
					"area": "Битрикс24: " + r["field"],
					"current": r["b24_value"],
					"expected": r["hr_value"],
				}
			)
		for r in b24.department_heads({"only_differences": 1})[1]:
			rows.append(
				{
					"person": None,
					"full_name": r.department_name,
					"area": "Битрикс24: руководитель",
					"current": r.b24_head_name or "—",
					"expected": r.hr_head_name or r.status,
				}
			)
	for r in frappe.db.sql(
		"""select a.person, p.full_name, a.employee_number from `tabAD Account` a join `tabPerson` p on p.name = a.person
		where a.enabled = 1 and a.missing_in_source = 0 and ifnull(a.employee_number, '') != a.person""",
		as_dict=True,
	):
		rows.append(
			{
				"person": r.person,
				"full_name": r.full_name,
				"area": "AD: employeeNumber",
				"current": r.employee_number or "—",
				"expected": r.person,
			}
		)
	return [
		_column("full_name", _("Кто или что"), "person"),
		_column("area", _("Где расхождение"), "badge"),
		_column("current", _("Сейчас")),
		_column("expected", _("Должно быть (по кадрам)")),
	], rows


# --------------------------------------------------------------------------- search and actions


@frappe.whitelist()
def search(query: str) -> list:
	_check()
	query = (query or "").strip()
	if len(query) < 2:
		return []
	like = f"%{query}%"
	results = []
	for p in frappe.db.sql(
		"select name, full_name, status from `tabPerson` where full_name like %s order by status = 'Работает' desc, full_name limit 8",
		like,
		as_dict=True,
	):
		results.append({"kind": "person", "id": p.name, "title": p.full_name, "subtitle": p.status})
	for doctype, title_field, extra, link in (
		("IB User", "user_name", "login", "1С"),
		("AD Account", "display_name", "sam_account_name", "AD"),
		("B24 User", "full_name", "email", "Битрикс24"),
	):
		for a in frappe.db.sql(
			f"""select name, {title_field} as title, {extra} as extra, person from `tab{doctype}`
			where missing_in_source = 0 and ({title_field} like %(like)s or {extra} like %(like)s) limit 5""",
			{"like": like},
			as_dict=True,
		):
			results.append(
				{
					"kind": "person" if a.person else "account",
					"id": a.person or a.name,
					"doctype": doctype,
					"title": a.title,
					"subtitle": f"{link}: {a.extra or ''}" + ("" if a.person else " · не привязан"),
				}
			)
	for r in frappe.get_all("Access Role", filters={"role_name": ["like", like]}, fields=["name"], limit=5):
		results.append({"kind": "role", "id": r.name, "title": r.name, "subtitle": _("роль доступа")})
	for p in frappe.get_all(
		"Business Process", filters={"title": ["like", like]}, fields=["name", "title"], limit=5
	):
		results.append({"kind": "process", "id": p.name, "title": p.title, "subtitle": _("бизнес-процесс")})
	for e in frappe.get_all(
		"Entitlement", filters={"title": ["like", like]}, fields=["name", "title", "system"], limit=5
	):
		results.append({"kind": "entitlement", "id": e.name, "title": e.title, "subtitle": e.system})
	return results


@frappe.whitelist(methods=["POST"])
def create_exception(person: str, entitlement: str, reason: str, valid_to: str | None = None) -> str:
	require(ROLE_MANAGER)
	if not (reason or "").strip():
		frappe.throw(_("Укажите, почему доступ согласован"))
	doc = frappe.get_doc(
		{
			"doctype": "Access Exception",
			"person": person,
			"entitlement": entitlement,
			"reason": reason.strip(),
			"valid_to": getdate(valid_to) if valid_to else None,
		}
	).insert()
	frappe.cache().delete_value(CACHE_KEY)
	return doc.name
