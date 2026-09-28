"""Data for the «Обзор доступа» page: overview, search, employee card, access matrix."""

from collections import defaultdict

import frappe
from frappe import _

from access_registry.access_catalog.access_report import load_profiles, main_places

ACTIVE_USER = "u.login_allowed = 1 and u.invalid = 0 and u.missing_in_source = 0"
# Service accounts and IB users without a card are not people: they have their own place.
UNLINKED = "ifnull(u.person, '') = '' and u.service = 0 and u.is_orphan = 0"
ATTENTION_LIMIT = 8
AD_ACTIVE = "a.enabled = 1 and a.missing_in_source = 0"
# 1C login allowed while the AD account behind it is disabled or gone
AD_OFF_1C_ON = f"{ACTIVE_USER} and (a.enabled = 0 or a.missing_in_source = 1)"


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
		"ad_domains": frappe.db.count("AD Domain"),
		"ad_enabled": _scalar(f"select count(*) from `tabAD Account` a where {AD_ACTIVE}"),
		"ad_not_working": _scalar(
			f"""select count(*) from `tabAD Account` a join `tabPerson` p on p.name = a.person
			where {AD_ACTIVE} and p.status != 'Работает'"""
		),
		"ad_unlinked": _scalar(
			f"select count(*) from `tabAD Account` a where {AD_ACTIVE} and ifnull(a.person, '') = ''"
		),
		"ad_off_1c_on": _scalar(
			f"""select count(*) from `tabIB User` u join `tabAD Account` a on a.name = u.ad_account
			where {AD_OFF_1C_ON}"""
		),
		"ad_groups_controlled": frappe.db.count("AD Group", {"under_control": 1, "missing_in_source": 0}),
	}
	return {
		"kpis": kpis,
		"bases": get_bases(),
		"domains": get_domains(),
		"attention": get_attention(),
	}


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


def get_domains() -> list:
	domains = frappe.get_all(
		"AD Domain",
		fields=["name", "title", "netbios_name", "dns_name", "enabled", "last_sync", "last_status"],
		order_by="name",
	)
	counts = {
		row[0]: row[1:]
		for row in frappe.db.sql(
			"""select a.domain, sum(a.enabled), count(*) from `tabAD Account` a
			where a.missing_in_source = 0 group by a.domain"""
		)
	}
	groups = dict(
		frappe.db.sql(
			"select domain, count(*) from `tabAD Group` where missing_in_source = 0 group by domain"
		)
	)
	for domain in domains:
		enabled, total = counts.get(domain.name, (0, 0))
		domain.enabled_accounts, domain.accounts = int(enabled or 0), int(total or 0)
		domain.groups = groups.get(domain.name, 0)
	return domains


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
	ad_not_working = frappe.db.sql(
		f"""select a.name, a.sam_account_name, a.domain, p.name as person, p.full_name, p.status
		from `tabAD Account` a join `tabPerson` p on p.name = a.person
		where {AD_ACTIVE} and p.status != 'Работает'
		order by p.full_name limit {ATTENTION_LIMIT}""",
		as_dict=True,
	)
	return {
		"not_working": not_working,
		"unlinked": unlinked,
		"extra_roles": extra,
		"ad_not_working": ad_not_working,
	}


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
	ad_accounts = frappe.db.sql(
		"""select name, display_name, sam_account_name, domain, enabled from `tabAD Account`
		where missing_in_source = 0 and ifnull(person, '') = ''
			and (display_name like %(like)s or sam_account_name like %(like)s
				or user_principal_name like %(like)s)
		order by display_name limit 6""",
		{"like": like},
		as_dict=True,
	)
	results += [
		{
			"kind": "ad",
			"id": a.name,
			"title": a.display_name,
			"status": "",
			"subtitle": " · ".join(
				(a.domain, a.sam_account_name or "", _("включена") if a.enabled else _("отключена"))
			)
			+ " · "
			+ _("не привязана"),
		}
		for a in ad_accounts
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
			"ad_account",
		],
		order_by="base_code",
	)
	ad_state = {
		row.name: row
		for row in frappe.get_all(
			"AD Account",
			filters={"name": ["in", [a.ad_account for a in accounts if a.ad_account] or [""]]},
			fields=["name", "enabled", "missing_in_source"],
		)
	}
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
		ad = ad_state.get(account.ad_account)
		account.ad_state = (
			"" if not ad else "missing" if ad.missing_in_source else "on" if ad.enabled else "off"
		)
	return accounts


def _ad_accounts(filters: dict) -> list:
	accounts = frappe.get_all(
		"AD Account",
		filters=filters,
		fields=[
			"name",
			"display_name",
			"sam_account_name",
			"user_principal_name",
			"domain",
			"enabled",
			"locked",
			"password_never_expires",
			"missing_in_source",
			"last_logon",
			"password_last_set",
			"title",
			"department",
			"ou",
			"employee_number",
			"person",
			"person_link_method",
			"person_link_note",
		],
		order_by="missing_in_source, enabled desc, domain",
	)
	groups = defaultdict(list)
	for row in frappe.get_all(
		"AD Account Group",
		filters={"parent": ["in", [a.name for a in accounts] or [""]], "parenttype": "AD Account"},
		fields=["parent", "group", "group_name"],
		order_by="group_name",
		limit_page_length=0,
	):
		groups[row.parent].append({"group": row.group, "name": row.group_name})
	for account in accounts:
		account.groups = groups.get(account.name, [])
		account.employee_number_ok = bool(
			account.person and (account.employee_number or "").lower() == account.person
		)
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
		"ad_accounts": _ad_accounts({"person": person}),
	}


@frappe.whitelist()
def get_account(account: str) -> dict:
	_check()
	return {"accounts": _accounts({"name": account}), "ad_accounts": []}


@frappe.whitelist()
def get_ad_account(account: str) -> dict:
	"""An AD account without an employee and the 1C users that log in with it."""
	_check()
	return {
		"ad_accounts": _ad_accounts({"name": account}),
		"accounts": _accounts({"ad_account": account, "missing_in_source": 0}),
	}


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
	domains = frappe.get_all("AD Domain", fields=["name", "title"], order_by="name")
	ad = defaultdict(dict)
	for row in frappe.db.sql(
		"""select a.person, a.domain, a.name, a.enabled, a.group_count from `tabAD Account` a
		where a.missing_in_source = 0 and ifnull(a.person, '') != ''
		order by a.enabled""",
		as_dict=True,
	):
		# an enabled account wins over a disabled one of the same person
		ad[row.person][row.domain] = {"account": row.name, "enabled": row.enabled, "groups": row.group_count}
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
		return {"bases": bases, "domains": domains, "rows": []}
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
				"ad": ad.get(person, {}),
			}
		)
	result.sort(key=lambda r: (r["organization"] or "", r["department"] or "", r["full_name"] or ""))
	return {"bases": bases, "domains": domains, "rows": result}


@frappe.whitelist()
def get_organizations() -> list:
	_check()
	return frappe.get_all(
		"HR Organization", filters={"missing": 0}, fields=["name", "title"], order_by="title"
	)
