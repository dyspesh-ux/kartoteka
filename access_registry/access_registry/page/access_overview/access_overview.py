"""Data for the «Обзор доступа» page: overview, search, employee card, access matrix."""

from collections import defaultdict

import frappe
from frappe import _

from access_registry.access_catalog.access_report import load_profiles, main_places

ACTIVE_USER = "u.login_allowed = 1 and u.invalid = 0 and u.missing_in_source = 0"
# Service accounts and IB users without a card are not people: they have their own place.
UNLINKED = "ifnull(u.person, '') = '' and u.service = 0 and u.is_orphan = 0"
ATTENTION_LIMIT = 8


def _check():
	frappe.only_for("System Manager")


def _scalar(sql, params=None):
	return frappe.db.sql(sql, params or {})[0][0] or 0


@frappe.whitelist()
def get_overview() -> dict:
	_check()
	kpis = {
		"employees": frappe.db.count("Person", {"status": "Работает"}),
		"long_absence": frappe.db.count("Person", {"presence": "Длительное отсутствие"}),
		"users": _scalar(f"select count(*) from `tabIB User` u where {ACTIVE_USER}"),
		"unlinked": _scalar(f"select count(*) from `tabIB User` u where {ACTIVE_USER} and {UNLINKED}"),
		"not_working": _scalar(
			f"""select count(*) from `tabIB User` u join `tabPerson` p on p.name = u.person
			where {ACTIVE_USER} and p.status != 'Работает'"""
		),
		"extra_roles": _scalar(
			f"select count(*) from `tabIB User` u where {ACTIVE_USER} and u.has_extra_roles = 1"
		),
		"orphans": frappe.db.count("IB User", {"is_orphan": 1, "missing_in_source": 0}),
		"merge_candidates": frappe.db.count("Person Merge Candidate", {"status": "Открыт"}),
		"new_events": frappe.db.count("HR Event", {"processed": 0}),
		"failed_syncs": frappe.db.count(
			"Sync Log", {"status": ["in", ["Ошибка", "Остановлен предохранителем"]]}
		),
	}
	return {"kpis": kpis, "bases": get_bases(), "attention": get_attention()}


def get_bases() -> list:
	bases = frappe.get_all(
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
			"itaccess_last_log",
		],
		order_by="configuration desc, name",
	)
	users = dict(
		frappe.db.sql(
			f"select u.base_code, count(*) from `tabIB User` u where {ACTIVE_USER} group by u.base_code"
		)
	)
	employees = dict(
		frappe.db.sql(
			"select source, count(*) from `tabEmployment` where status in ('Работает', 'Увольняется') group by source"
		)
	)
	for base in bases:
		base.users = users.get(base.name, 0)
		base.employments = employees.get(base.name, 0)
	return bases


def get_attention() -> dict:
	not_working = frappe.db.sql(
		f"""select u.name, u.user_name, u.base_code, u.login, p.name as person, p.full_name, p.status
		from `tabIB User` u join `tabPerson` p on p.name = u.person
		where {ACTIVE_USER} and p.status != 'Работает'
		order by p.full_name limit {ATTENTION_LIMIT}""",
		as_dict=True,
	)
	unlinked = frappe.db.sql(
		f"""select u.name, u.user_name, u.base_code, u.login, u.person_link_note
		from `tabIB User` u where {ACTIVE_USER} and {UNLINKED}
		order by u.user_name limit {ATTENTION_LIMIT}""",
		as_dict=True,
	)
	extra = frappe.db.sql(
		f"""select u.name, u.user_name, u.base_code, u.extra_roles, u.person
		from `tabIB User` u where {ACTIVE_USER} and u.has_extra_roles = 1
		order by u.user_name limit {ATTENTION_LIMIT}""",
		as_dict=True,
	)
	for row in extra:
		roles = [r for r in (row.extra_roles or "").splitlines() if r]
		row.roles = roles[:4]
		row.more = max(0, len(roles) - 4)
	return {"not_working": not_working, "unlinked": unlinked, "extra_roles": extra}


@frappe.whitelist()
def search(query: str) -> list:
	"""Employees by name, 1C users by name, login or AD login."""
	_check()
	query = (query or "").strip()
	if len(query) < 2:
		return []
	like = f"%{query}%"
	people = frappe.db.sql(
		"""select name, full_name, status from `tabPerson`
		where full_name like %(like)s order by status = 'Работает' desc, full_name limit 8""",
		{"like": like},
		as_dict=True,
	)
	places = main_places([p.name for p in people])
	results = [
		{
			"kind": "person",
			"id": p.name,
			"title": p.full_name,
			"status": p.status,
			"subtitle": " · ".join(
				x
				for x in (
					places.get(p.name, {}).get("position_title"),
					places.get(p.name, {}).get("organization_title"),
				)
				if x
			),
		}
		for p in people
	]
	accounts = frappe.db.sql(
		"""select name, user_name, base_code, login, ad_login, person from `tabIB User`
		where missing_in_source = 0 and ifnull(person, '') = ''
			and (user_name like %(like)s or login like %(like)s or ad_login like %(like)s)
		order by user_name limit 6""",
		{"like": like},
		as_dict=True,
	)
	results += [
		{
			"kind": "account",
			"id": a.name,
			"title": a.user_name,
			"status": "",
			"subtitle": " · ".join(x for x in (a.base_code, a.login, a.ad_login) if x)
			+ " · "
			+ _("не привязан"),
		}
		for a in accounts
	]
	return results


def _accounts(filters: dict) -> list:
	accounts = frappe.get_all(
		"IB User",
		filters=filters,
		fields=[
			"name",
			"user_name",
			"base_code",
			"base_configuration",
			"login",
			"ad_login",
			"login_allowed",
			"invalid",
			"is_orphan",
			"missing_in_source",
			"orgs_text",
			"all_orgs",
			"extra_roles",
			"has_extra_roles",
			"person",
			"person_link_method",
			"person_link_note",
		],
		order_by="base_code",
	)
	profiles = load_profiles([a.name for a in accounts])
	for account in accounts:
		account.profiles = [
			{
				"profile": p.profile_name,
				"group": p.access_group_name,
				"orgs": p.orgs_text,
				"restrictions": [line for line in (p.restrictions_text or "").splitlines() if line],
			}
			for p in profiles.get(account.name, [])
		]
		account.extra_roles = [r for r in (account.extra_roles or "").splitlines() if r]
	return accounts


@frappe.whitelist()
def get_person(person: str) -> dict:
	_check()
	doc = frappe.get_doc("Person", person)
	employments = frappe.db.sql(
		"""select e.name, e.status, e.employment_kind, e.hire_date, e.termination_date, e.tab_number,
			e.category, e.state, o.title as organization, d.title as department, pos.title as position,
			e.source
		from `tabEmployment` e
			left join `tabHR Organization` o on o.name = e.organization
			left join `tabHR Department` d on d.name = e.department
			left join `tabHR Position` pos on pos.name = e.position
		where e.person = %(person)s
		order by e.status in ('Работает', 'Увольняется') desc, e.hire_date desc""",
		{"person": person},
		as_dict=True,
	)
	events = frappe.get_all(
		"HR Event",
		filters={"person": person},
		fields=["event_type", "event_date", "details", "processed"],
		order_by="event_date desc",
		limit=8,
	)
	place = main_places([person]).get(person, {})
	return {
		"person": {
			"name": doc.name,
			"full_name": doc.full_name,
			"status": doc.status,
			"presence": doc.presence,
			"external_part_time_only": doc.external_part_time_only,
			"position": place.get("position_title"),
			"department": place.get("department_title"),
			"organization": place.get("organization_title"),
		},
		"employments": employments,
		"events": events,
		"accounts": _accounts({"person": person, "missing_in_source": 0}),
	}


@frappe.whitelist()
def get_account(account: str) -> dict:
	_check()
	return {"accounts": _accounts({"name": account})}


@frappe.whitelist()
def get_matrix(organization: str | None = None, only_working: int = 1) -> dict:
	"""Employees × bases: who has access where."""
	_check()
	bases = frappe.get_all(
		"Info Base",
		filters={"itaccess_enabled": 1},
		fields=["name", "title", "configuration"],
		order_by="name",
	)
	rows = frappe.db.sql(
		f"""select u.person, u.base_code, u.name, u.has_extra_roles, u.all_orgs
		from `tabIB User` u where {ACTIVE_USER} and ifnull(u.person, '') != ''""",
		as_dict=True,
	)
	profiles = load_profiles([r.name for r in rows])
	cells = defaultdict(dict)
	for row in rows:
		cells[row.person][row.base_code] = {
			"account": row.name,
			"profiles": sorted({p.profile_name for p in profiles.get(row.name, []) if p.profile_name}),
			"extra": row.has_extra_roles,
			"all_orgs": row.all_orgs,
		}
	persons = list(cells)
	if not persons:
		return {"bases": bases, "rows": []}
	people = {
		p.name: p
		for p in frappe.get_all(
			"Person",
			filters={"name": ["in", persons]},
			fields=["name", "full_name", "status"],
			limit_page_length=0,
		)
	}
	places = main_places(persons)
	result = []
	for person in persons:
		p = people.get(person)
		if not p:
			continue
		if int(only_working or 0) and p.status != "Работает":
			continue
		place = places.get(person, {})
		if organization and place.get("organization") != organization:
			continue
		result.append(
			{
				"person": person,
				"full_name": p.full_name,
				"status": p.status,
				"position": place.get("position_title"),
				"department": place.get("department_title"),
				"organization": place.get("organization_title"),
				"cells": cells[person],
			}
		)
	result.sort(key=lambda r: (r["organization"] or "", r["department"] or "", r["full_name"] or ""))
	return {"bases": bases, "rows": result}


@frappe.whitelist()
def get_organizations() -> list:
	_check()
	return frappe.get_all(
		"HR Organization", filters={"missing": 0}, fields=["name", "title"], order_by="title"
	)
