"""API of the registry app for end users (/registry): security, internal audit, rights controllers.

Every method checks the registry roles itself (see permissions.py). Personal data (birth dates)
goes only to administrators and auditors.
"""

from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, today

from access_registry import app_access as aa
from access_registry.access_catalog.access_report import main_places
from access_registry.access_roles import engine, suppression
from access_registry.app_access import CONTROLS, VIEW, WORK
from access_registry.permissions import (
	ADMINS,
	AUDITOR,
	PROCESS_MANAGER,
	REVIEWER,
	ROLE_MANAGER,
	has_any,
	require,
)

LIST_LIMIT = 500
CACHE_KEY = "access_registry:registry_dashboard"
STALE_DAYS = 90


def _check(section: str, need: int = VIEW):
	"""Access to a section of the app: by registry roles or by access profiles (app_access)."""
	aa.require_section(section, need)


def _personal() -> bool:
	return aa.personal()


def _reviewer():
	"""Own review tasks: anybody who may open the app (the tasks are filtered by the reviewer)."""
	if not aa.can_open_app():
		raise frappe.PermissionError(_("Нет доступа к реестру"))


def _column(key, label, kind="text", link=None):
	return {"key": key, "label": label, "type": kind, "link": link}


# --------------------------------------------------------------------------- basics


@frappe.whitelist()
def bootstrap() -> dict:
	"""Who the user is and what the app shows. Reviewers without other roles see only their tasks."""
	if not aa.can_open_app():
		raise frappe.PermissionError(_("Нет доступа к реестру"))
	from access_registry.access_roles.review import pending_count

	user = frappe.get_cached_doc("User", frappe.session.user)
	a = aa.access()
	return {
		"pending_reviews": pending_count(),
		"user": {"name": user.name, "full_name": user.full_name or user.name, "image": user.user_image},
		"can": {
			"read": any(a["sections"].values()),
			"sections": a["sections"],
			"control_lists": aa.control_lists(),
			"via": a["via"],
			"personal": a["personal"],
			# desk editing needs desk roles; the app only links to the desk forms
			"roles": has_any(*ADMINS, ROLE_MANAGER),
			"processes": has_any(*ADMINS, PROCESS_MANAGER),
			"admin": has_any(*ADMINS),
			"exceptions": a["sections"]["roles"] >= WORK,
			"suppress": a["sections"]["control"] >= WORK,
			"reviewer": bool(a.get("reviewer")),
			"search": any(a["sections"][s] for s in ("people", "roles", "processes", "access")),
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
	"""Counters for «Обзор», and for the list chips of «Контроль» (trimmed to what the user may see)."""
	if not (aa.has_section("overview") or aa.has_section("control") or aa.has_section("sources")):
		aa.require_section("overview")
	data = None if cint(refresh) else frappe.cache().get_value(CACHE_KEY)
	if not data:
		data = _dashboard()
		frappe.cache().set_value(CACHE_KEY, data, expires_in_sec=300)
	return _trim_dashboard(data)


# which dashboard counters belong to which control list
DASHBOARD_LISTS = {
	"dismissed_access": "dismissed",
	"unlinked": "unlinked",
	"sod": "sod",
	"events": "events",
	"processes": "processes",
	"suppressed": "journal",
}


def _trim_dashboard(data: dict) -> dict:
	if aa.has_section("overview"):
		return data
	lists = set(aa.control_lists())
	trimmed = {
		"generated": data["generated"],
		"sources": data["sources"] if aa.has_section("sources") else [],
	}
	for key, kind in DASHBOARD_LISTS.items():
		if kind in lists:
			trimmed[key] = data[key]
	if lists & {"excess", "missing", "exceptions"}:
		trimmed["reconciliation"] = data["reconciliation"]
	if "journal" in lists and not aa.access()["all_lists"]:
		# suppressions of the user's lists only
		trimmed["suppressed"] = sum(
			1
			for s in suppression.active().values()
			if frappe.db.get_value("Alert Suppression", s.name, "alert_kind") in lists
		)
	return trimmed


def _dashboard() -> dict:
	persons = frappe.get_all("Person", fields=["name", "status"], limit_page_length=0)
	working = [p.name for p in persons if p.status == "Работает"]
	# counters show open alerts only: suppressed ones are in the journal
	suppressed = suppression.active()
	dismissed_rows = suppression.split("dismissed", _control_dismissed()[1], suppressed)[0]
	dismissed = {r.person for r in dismissed_rows}
	by_system = Counter(system for system, _person in {(r.system, r.person) for r in dismissed_rows})
	unlinked = Counter({"1С": 0, "AD": 0, "Битрикс24": 0})
	for r in suppression.split("unlinked", _control_unlinked()[1], suppressed)[0]:
		unlinked[r.system] += 1

	model = engine.RoleModel()
	has_model = bool(model.roles or model.process_roles)
	statuses = Counter()
	privileged = 0
	if has_model or frappe.db.count("Entitlement"):
		for row in engine.reconcile(model=model):
			statuses[row["status"]] += 1
			if row["privileged"] and row["status"] != engine.MISSING:
				privileged += 1
	sod = (
		len(suppression.split("sod", engine.sod_conflicts(), suppressed)[0])
		if frappe.db.count("SoD Rule", {"active": 1})
		else 0
	)

	from access_registry.business_processes.reports import continuity

	process_risks = (
		len(suppression.split("processes", continuity({"only_problems": 1})[1], suppressed)[0])
		if model.process_roles
		else 0
	)
	expiring = frappe.db.count(
		"Access Exception", {"valid_to": ["between", [today(), add_days(today(), 14)]]}
	) + frappe.db.count("Access Role Assignment", {"valid_to": ["between", [today(), add_days(today(), 14)]]})
	return {
		"generated": str(now_datetime()),
		"people": {"working": len(working), "total": len(persons)},
		"dismissed_access": {"people": len(dismissed), "by_system": dict(by_system)},
		"unlinked": dict(unlinked),
		"suppressed": len(suppressed),
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
		"events": frappe.db.count(
			"HR Event", {"processed": 0, "event_type": ["in", JML_GRANT + JML_REVOKE + JML_PLAN]}
		),
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
	for f in frappe.get_all("File Server", fields=["name", "title", "enabled", "last_upload", "last_status"]):
		result.append(
			{
				"kind": "Общие папки Synology",
				"name": f.name,
				"title": f.title,
				"enabled": f.enabled,
				"last": f.last_upload,
				"status": f.last_status,
				"doctype": "File Server",
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
	_check("people")
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
	_check("people")
	return frappe.get_all(
		"HR Organization", filters={"missing": 0}, fields=["name", "title"], order_by="title"
	)


@frappe.whitelist()
def person(name: str) -> dict:
	_check("people")
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
	shares = []
	if frappe.db.count("Folder ACL"):
		from access_registry.file_shares.access import ShareAccess

		shares = [
			{k: row[k] for k in ("share_name", "path", "level", "via", "login", "server")}
			for row in ShareAccess().rows({"person": name})
		]
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
		"shares": shares,
		"roles": roles,
		"process_roles": process_roles,
		"reconciliation": engine.reconcile({name}, model),
		"sod": engine.sod_conflicts({name}),
	}


# --------------------------------------------------------------------------- catalog, roles, processes


@frappe.whitelist()
def entitlements(system: str | None = None) -> list:
	_check("access")
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
	_check("access")
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
	_check("roles")
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
	_check("roles")
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
	_check("processes")
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
	_check("processes")
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


JML_GRANT = ("Приём", "Перевод", "Выход из отпуска по уходу", "Вернулся в выгрузку")
JML_REVOKE = ("Увольнение", "Пропал из выгрузки")
JML_PLAN = ("Предстоящее увольнение", "Уход в отпуск по уходу")


def _control_rows(kind):
	if kind not in CONTROLS:
		frappe.throw(_("Неизвестный раздел контроля"))
	return globals()[f"_control_{kind}"]()


@frappe.whitelist()
def control(kind: str, show_suppressed: int = 0) -> dict:
	"""A control list without suppressed alerts; with show_suppressed — only the suppressed ones."""
	aa.require_control(kind)
	columns, rows = _control_rows(kind)
	open_rows, hidden = suppression.split(kind, rows)
	shown = hidden if cint(show_suppressed) else open_rows
	return {
		"kind": kind,
		"title": CONTROLS[kind],
		"columns": columns,
		"rows": shown[:5000],
		"total": len(shown),
		"suppressible": kind in suppression.SUPPRESSIBLE,
		"suppressed": len(hidden),
	}


@frappe.whitelist(methods=["POST"])
def suppress_alerts(kind: str, keys, reason: str, valid_to: str | None = None) -> int:
	"""Suppresses the chosen alerts of a control list with a reason; every one gets a journal entry."""
	aa.require_control(kind, WORK)
	keys = frappe.parse_json(keys) if isinstance(keys, str) else keys
	created = suppression.suppress(
		kind, CONTROLS.get(kind, kind), _control_rows(kind)[1], keys, reason, valid_to
	)
	frappe.cache().delete_value(CACHE_KEY)
	return len(created)


@frappe.whitelist(methods=["POST"])
def restore_alert(name: str, reason: str) -> str:
	"""The alert shows in its list again; the journal keeps who returned it and why."""
	# only alerts of the lists the user works with (the journal alone does not give that)
	aa.require_control(frappe.db.get_value("Alert Suppression", name, "alert_kind"), WORK)
	result = suppression.restore(name, reason)
	frappe.cache().delete_value(CACHE_KEY)
	return result


def _control_journal():
	rows = suppression.journal()
	allowed = set(aa.control_lists())
	if not aa.access()["all_lists"]:
		rows = [r for r in rows if r["alert_kind"] in allowed]  # only the lists the user works with
	for r in rows:
		r["ref"], r["ref_doctype"] = r["name"], "Alert Suppression"
		r["when"] = r["suppressed_on"]
		r["until"] = r["valid_to"] or ("бессрочно" if r["status"] == suppression.ACTIVE else "")
		r["returned"] = (
			f"{r['restored_by_name']}, {r['restored_on'][:16]}: {r['restore_reason']}"
			if r["restored_by"]
			else ""
		)
	return [
		_column("when", _("Когда"), "datetime"),
		_column("status", _("Статус"), "badge"),
		_column("alert_title", _("Замечание")),
		_column("subject", _("Что"), "ref"),
		_column("reason", _("Почему погашено")),
		_column("suppressed_by_name", _("Погасил")),
		_column("until", _("До")),
		_column("returned", _("Возврат")),
	], rows


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


def _control_events():
	"""Joiners, movers, leavers: what to grant and what to revoke after an HR event."""
	events = frappe.db.sql(
		"""select ev.name, ev.event_type, ev.event_date, ev.person, ev.details, p.full_name, p.status as person_status
		from `tabHR Event` ev join `tabPerson` p on p.name = ev.person
		where ev.processed = 0 and ev.event_type in %(types)s
		order by ev.event_date desc, p.full_name limit 1000""",
		{"types": JML_GRANT + JML_REVOKE + JML_PLAN},
		as_dict=True,
	)
	persons = {e.person for e in events}
	by_person = defaultdict(list)
	if persons:
		for row in engine.reconcile(persons):
			by_person[row["person"]].append(row)
	accounts = Accounts()
	for ev in events:
		recon = by_person.get(ev.person, [])
		grant = [r["title"] for r in recon if r["status"] == engine.MISSING]
		revoke = [r["title"] for r in recon if r["status"] in (engine.EXCESS, engine.EXCESS_NOT_WORKING)]
		active = [
			f"{system}: {n}" if n > 1 else system for system, n in accounts.active(ev.person).items() if n
		]
		todo = []
		if ev.event_type in JML_GRANT:
			if grant:
				todo.append(_("Выдать: {0}").format(", ".join(grant)))
			if ev.event_type == "Перевод" and revoke:
				todo.append(_("Отозвать лишнее после перевода: {0}").format(", ".join(revoke)))
			if not active and ev.person_status == "Работает":
				todo.append(_("Учёток ещё нет: завести"))
		elif ev.event_type in JML_REVOKE:
			if active:
				todo.append(_("Отключить учётки: {0}").format(", ".join(active)))
			if revoke:
				todo.append(_("Отозвать: {0}").format(", ".join(revoke)))
		elif ev.event_type == "Предстоящее увольнение":
			if active:
				todo.append(
					_("Запланировать отключение в последний рабочий день: {0}").format(", ".join(active))
				)
		elif active:
			todo.append(_("Решить, блокировать ли учётки на время отпуска: {0}").format(", ".join(active)))
		ev.todo = "; ".join(todo) or _("действий не требуется")
		ev.event_date = str(ev.event_date) if ev.event_date else None
		ev.ref, ev.ref_doctype = ev.name, "HR Event"
	return [
		_column("event_date", _("Дата"), "date"),
		_column("event_type", _("Событие"), "badge"),
		_column("full_name", _("Сотрудник"), "person"),
		_column("todo", _("Что сделать")),
		_column("details", _("Подробности")),
	], events


def _control_shares():
	from access_registry.file_shares.reports import share_issues

	rows = share_issues({})[1]
	persons = {r["person"] for r in rows if r.get("person")}
	names = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", list(persons) or [""]]},
			fields=["name", "full_name"],
			as_list=True,
		)
	)
	for r in rows:
		r["full_name"] = names.get(r.get("person")) or ""
		r["ref"], r["ref_doctype"] = r["folder"], "Folder ACL"
	return [
		_column("share_name", _("Общая папка"), "badge"),
		_column("path", _("Папка"), "ref"),
		_column("issue", _("Замечание")),
		_column("detail", _("Подробно")),
	], rows


# --------------------------------------------------------------------------- search and actions


@frappe.whitelist()
def search(query: str) -> list:
	if not aa.any_section():
		aa.require_section("people")
	people, roles_ok = aa.has_section("people"), aa.has_section("roles")
	processes_ok, catalog_ok = aa.has_section("processes"), aa.has_section("access")
	query = (query or "").strip()
	if len(query) < 2:
		return []
	like = f"%{query}%"
	results = []
	for p in (
		frappe.db.sql(
			"select name, full_name, status from `tabPerson` where full_name like %s order by status = 'Работает' desc, full_name limit 8",
			like,
			as_dict=True,
		)
		if people
		else []
	):
		results.append({"kind": "person", "id": p.name, "title": p.full_name, "subtitle": p.status})
	for doctype, title_field, extra, link in (
		(
			("IB User", "user_name", "login", "1С"),
			("AD Account", "display_name", "sam_account_name", "AD"),
			("B24 User", "full_name", "email", "Битрикс24"),
		)
		if people
		else ()
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
	for r in (
		frappe.get_all("Access Role", filters={"role_name": ["like", like]}, fields=["name"], limit=5)
		if roles_ok
		else []
	):
		results.append({"kind": "role", "id": r.name, "title": r.name, "subtitle": _("роль доступа")})
	for p in (
		frappe.get_all(
			"Business Process", filters={"title": ["like", like]}, fields=["name", "title"], limit=5
		)
		if processes_ok
		else []
	):
		results.append({"kind": "process", "id": p.name, "title": p.title, "subtitle": _("бизнес-процесс")})
	for e in (
		frappe.get_all(
			"Entitlement", filters={"title": ["like", like]}, fields=["name", "title", "system"], limit=5
		)
		if catalog_ok
		else []
	):
		results.append({"kind": "entitlement", "id": e.name, "title": e.title, "subtitle": e.system})
	return results


@frappe.whitelist(methods=["POST"])
def create_exception(person: str, entitlement: str, reason: str, valid_to: str | None = None) -> str:
	_check("roles", WORK)
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


# --------------------------------------------------------------------------- access reviews


@frappe.whitelist()
def my_reviews() -> list:
	"""Tasks of the current user in running access reviews."""
	_reviewer()
	from access_registry.access_roles.review import my_items

	return my_items()


@frappe.whitelist(methods=["POST"])
def decide(item: str, decision: str, comment: str | None = None) -> dict:
	_reviewer()
	from access_registry.access_roles.review import decide as _decide

	return _decide(item, decision, comment)


@frappe.whitelist(methods=["POST"])
def decide_person(review: str, person: str, decision: str) -> int:
	"""The same decision for all undecided accesses of one employee assigned to the current user."""
	_reviewer()
	from access_registry.access_roles.review import decide as _decide

	items = frappe.get_all(
		"Access Review Item",
		filters={
			"access_review": review,
			"person": person,
			"reviewer_user": frappe.session.user,
			"decision": ["in", ["", None]],
		},
		pluck="name",
	)
	for name in items:
		_decide(name, decision)
	return len(items)


@frappe.whitelist()
def reviews() -> list:
	_check("reviews")
	rows = frappe.get_all(
		"Access Review",
		fields=[
			"name",
			"title",
			"status",
			"due_date",
			"reviewer_mode",
			"items_total",
			"items_done",
			"items_revoke",
			"items_unassigned",
			"started_on",
			"finished_on",
		],
		order_by="creation desc",
	)
	for r in rows:
		r.due_date = str(r.due_date) if r.due_date else None
	return rows


@frappe.whitelist()
def review(name: str) -> dict:
	_check("reviews")
	from access_registry.access_roles.review import results

	doc = frappe.get_doc("Access Review", name)
	return {
		"doc": {
			k: doc.get(k)
			for k in (
				"name",
				"title",
				"status",
				"reviewer_mode",
				"description",
				"items_total",
				"items_done",
				"items_revoke",
				"items_unassigned",
			)
		}
		| {"due_date": str(doc.due_date) if doc.due_date else None},
		"items": results(name),
	}


@frappe.whitelist(methods=["POST"])
def mark_event_processed(event: str) -> str:
	"""An HR event is handled: access granted or revoked in the systems."""
	aa.require_control("events", WORK)
	frappe.db.set_value("HR Event", event, "processed", 1)
	frappe.cache().delete_value(CACHE_KEY)
	return event


# --------------------------------------------------------------------------- access to the app


PROFILE_FIELDS = {
	"overview": "s_overview",
	"people": "s_people",
	"control": "s_control",
	"access": "s_access",
	"roles": "s_roles",
	"processes": "s_processes",
	"reviews": "s_reviews",
	"sources": "s_sources",
}
LEVEL_TITLE = {aa.NONE: "", VIEW: "Просмотр", WORK: "Работа"}


def _profile_dict(p) -> dict:
	sections, personal, lists = aa.profile_access(p)
	return {
		"name": p.name,
		"profile_name": p.profile_name,
		"enabled": p.enabled,
		"description": p.description,
		"sections": sections,
		"personal": personal,
		"control_lists": lists,
		"members": [{"user": m.user, "full_name": m.full_name or m.user} for m in p.members],
	}


@frappe.whitelist()
def access_admin() -> dict:
	"""Profiles, who sees what and why — for the administrators of the registry."""
	require(*ADMINS)
	profiles = [
		_profile_dict(frappe.get_doc("Registry Access Profile", n))
		for n in frappe.get_all("Registry Access Profile", order_by="profile_name", pluck="name")
	]
	registry_roles = [*ADMINS, AUDITOR, ROLE_MANAGER, PROCESS_MANAGER, aa.VIEWER, REVIEWER]
	users = set(
		frappe.get_all(
			"Has Role", filters={"parenttype": "User", "role": ["in", registry_roles]}, pluck="parent"
		)
	)
	users |= set(
		frappe.get_all(
			"Registry Access Member", filters={"parenttype": "Registry Access Profile"}, pluck="user"
		)
	)
	users -= {"Administrator", "Guest"}
	people = []
	for u in frappe.get_all(
		"User",
		filters={"name": ["in", list(users) or [""]], "enabled": 1},
		fields=["name", "full_name"],
		order_by="full_name",
	):
		a = aa.access(u.name)
		if not (any(a["sections"].values()) or a.get("reviewer")):
			continue
		people.append(
			{
				"user": u.name,
				"full_name": u.full_name or u.name,
				"sections": a["sections"],
				"personal": a["personal"],
				"lists": None if a["all_lists"] else sorted(a["control_lists"]),
				"via": a["via"] or ([_("только свои задания пересмотра")] if a.get("reviewer") else []),
			}
		)
	return {
		"sections": aa.SECTIONS,
		"work_sections": sorted(aa.WORK_SECTIONS),
		"controls": CONTROLS,
		"profiles": profiles,
		"users": people,
		"roles": [
			{"role": _("Администратор (Registry Admin)"), "gives": _("всё, включая управление доступом")},
			{
				"role": AUDITOR,
				"gives": _("все разделы на просмотр, «Контроль» — работа, персональные данные"),
			},
			{
				"role": ROLE_MANAGER,
				"gives": _("все разделы на просмотр, «Контроль» и «Роли доступа» — работа"),
			},
			{"role": PROCESS_MANAGER, "gives": _("все разделы на просмотр")},
			{"role": aa.VIEWER, "gives": _("все разделы на просмотр, без персональных данных")},
			{"role": REVIEWER, "gives": _("только свои задания пересмотра доступа")},
		],
	}


@frappe.whitelist(methods=["POST"])
def save_profile(data) -> str:
	"""Creates or updates an access profile; every change is kept in its version history."""
	require(*ADMINS)
	data = frappe.parse_json(data) if isinstance(data, str) else frappe._dict(data)
	name = (data.get("profile_name") or "").strip()
	if not name:
		frappe.throw(_("Назовите профиль"))
	doc = (
		frappe.get_doc("Registry Access Profile", data["name"])
		if data.get("name")
		else frappe.new_doc("Registry Access Profile")
	)
	doc.profile_name = name
	doc.enabled = cint(data.get("enabled", 1))
	doc.description = data.get("description")
	sections = data.get("sections") or {}
	for section, field in PROFILE_FIELDS.items():
		value = cint(sections.get(section))
		if section in aa.WORK_SECTIONS:
			doc.set(field, LEVEL_TITLE[min(value, WORK)])
		else:
			doc.set(field, 1 if value else 0)
	doc.s_personal = cint(data.get("personal"))
	doc.set(
		"control_lists",
		[{"control_list": CONTROLS[k]} for k in data.get("control_lists") or [] if k in CONTROLS],
	)
	users = []
	for user in data.get("members") or []:
		if user not in users and frappe.db.exists("User", user):
			users.append(user)
	doc.set("members", [{"user": u} for u in users])
	doc.save(ignore_permissions=True, ignore_version=False)
	return doc.name


@frappe.whitelist()
def find_users(query: str) -> list:
	require(*ADMINS)
	like = f"%{(query or '').strip()}%"
	return frappe.db.sql(
		"""select name as user, full_name from `tabUser`
		where enabled = 1 and name not in ('Guest', 'Administrator') and user_type in ('System User', 'Website User')
			and (full_name like %(like)s or name like %(like)s)
		order by full_name limit 10""",
		{"like": like},
		as_dict=True,
	)


@frappe.whitelist()
def profile_history(name: str) -> list:
	"""Who changed the profile, when and what."""
	require(*ADMINS)
	rows = []
	for v in frappe.get_all(
		"Version",
		filters={"ref_doctype": "Registry Access Profile", "docname": name},
		fields=["owner", "creation", "data"],
		order_by="creation desc",
		limit=50,
	):
		diff = frappe.parse_json(v.data or "{}")
		parts = [f"{c[0]}: {c[1] or '—'} → {c[2] or '—'}" for c in diff.get("changed") or []]
		for table, rows_ in (("добавлено", diff.get("added") or []), ("убрано", diff.get("removed") or [])):
			for field, row in rows_:
				parts.append(f"{table}: {row.get('user') or row.get('control_list') or field}")
		rows.append(
			{
				"when": str(v.creation),
				"who": frappe.db.get_value("User", v.owner, "full_name") or v.owner,
				"what": "; ".join(parts) or _("создан"),
			}
		)
	return rows
