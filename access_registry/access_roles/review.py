"""Access review (пересмотр доступа): reviewers confirm or revoke what employees actually have.

A campaign takes a snapshot of actual accesses (catalogued entitlements and, optionally, accesses
outside the catalog), assigns each to a reviewer — the employee's manager from the HR data, the
owner of the entitlement or one reviewer — and collects decisions «Оставить» / «Отозвать».
The registry does not revoke anything itself: the result is a list for administrators.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import now_datetime

from access_registry.access_catalog.access_report import main_places
from access_registry.access_roles import engine

KEEP = "Оставить"
REVOKE = "Отозвать"
DECISIONS = (KEEP, REVOKE)
SYSTEM_OF_KEY = {"1c": "1С", "ad": "Active Directory", "b24wg": "Битрикс24", "b24": "Битрикс24"}
HIGH_RISK = ("Высокий", "Критичный")


def person_users() -> dict:
	"""Employee → Frappe user, matched by e-mail from AD (mail, UPN) and Bitrix24."""
	users = {
		(u.email or u.name).lower(): u.name
		for u in frappe.get_all("User", filters={"enabled": 1}, fields=["name", "email"], limit_page_length=0)
	}
	result = {}
	sources = frappe.db.sql(
		"""select person, mail from `tabAD Account` where ifnull(person, '') != '' and ifnull(mail, '') != ''
		union all select person, user_principal_name from `tabAD Account`
			where ifnull(person, '') != '' and ifnull(user_principal_name, '') != ''
		union all select person, email from `tabB24 User` where ifnull(person, '') != '' and ifnull(email, '') != ''"""
	)
	for person, email in sources:
		user = users.get((email or "").lower())
		if user and person not in result:
			result[person] = user
	return result


def managers(persons) -> dict:
	"""Employee → head of the department of the main place of work (the parent's head for heads)."""
	places = main_places(list(persons))
	departments = {
		d.name: d
		for d in frappe.get_all(
			"HR Department", fields=["name", "head", "parent_hr_department"], limit_page_length=0
		)
	}
	result = {}
	for person in persons:
		dept = (places.get(person) or {}).get("department")
		seen = set()
		while dept and dept not in seen:
			seen.add(dept)
			row = departments.get(dept)
			if not row:
				break
			if row.head and row.head != person:
				result[person] = row.head
				break
			dept = row.parent_hr_department
	return result


def collect_items(review) -> list[dict]:
	"""Accesses in the scope of the review, one dict per (person, access)."""
	actual, other = engine.actual_entitlements()
	entitlements = {
		e.name: e
		for e in frappe.get_all(
			"Entitlement",
			fields=["name", "title", "system", "risk", "privileged", "owner_person"],
			limit_page_length=0,
		)
	}
	persons = set(actual) | (set(other) if review.include_uncatalogued else set())
	people = {
		p.name: p
		for p in frappe.get_all(
			"Person", filters={"name": ["in", list(persons) or [""]]}, fields=["name", "full_name", "status"],
			limit_page_length=0,
		)
	}
	places = main_places(list(persons)) if review.organization else {}
	items = []
	for person in sorted(persons, key=lambda p: (people.get(p) or {}).get("full_name") or ""):
		info = people.get(person)
		if not info:
			continue
		if review.organization and (places.get(person) or {}).get("organization") != review.organization:
			continue
		for name, evidence in actual.get(person, {}).items():
			e = entitlements.get(name)
			if not e:
				continue
			if review.system not in (None, "", "Все") and e.system != review.system:
				continue
			if review.only_privileged and not (e.privileged or e.risk in HIGH_RISK):
				continue
			items.append(
				{
					"person": person, "full_name": info.full_name, "person_status": info.status,
					"system": e.system, "entitlement": name, "access_title": e.title, "access_key": None,
					"risk": e.risk, "evidence": evidence, "owner": e.owner_person,
				}
			)
		if review.include_uncatalogued and not review.only_privileged:
			for key, evidence in other.get(person, {}).items():
				system = SYSTEM_OF_KEY.get(key.split(":", 1)[0], "")
				if review.system not in (None, "", "Все") and system != review.system:
					continue
				items.append(
					{
						"person": person, "full_name": info.full_name, "person_status": info.status,
						"system": system, "entitlement": None, "access_title": engine.describe_key(key)["title"],
						"access_key": key, "risk": "", "evidence": evidence, "owner": None,
					}
				)
	return items


def start_review(name: str) -> dict:
	review = frappe.get_doc("Access Review", name)
	if review.status != "Черновик":
		frappe.throw(_("Пересмотр уже начат"))
	if review.reviewer_mode == "Один проверяющий" and not review.reviewer:
		frappe.throw(_("Укажите проверяющего"))
	items = collect_items(review)
	users = person_users()
	heads = managers({i["person"] for i in items}) if review.reviewer_mode == "Руководитель сотрудника" else {}
	for item in items:
		if review.reviewer_mode == "Руководитель сотрудника":
			reviewer = users.get(heads.get(item["person"]))
		elif review.reviewer_mode == "Владелец права":
			reviewer = users.get(item["owner"])
		else:
			reviewer = None
		frappe.get_doc(
			{
				"doctype": "Access Review Item",
				"access_review": review.name,
				**{k: v for k, v in item.items() if k != "owner"},
				"reviewer_user": reviewer or review.reviewer,
			}
		).insert(ignore_permissions=True)
	review.status = "Идёт"
	review.started_on = now_datetime()
	review.save(ignore_permissions=True)
	return refresh(review.name)


def refresh(name: str) -> dict:
	counts = frappe.db.sql(
		"""select count(*), sum(ifnull(decision, '') != ''), sum(decision = %s),
			sum(ifnull(reviewer_user, '') = '')
		from `tabAccess Review Item` where access_review = %s""",
		(REVOKE, name),
	)[0]
	values = {
		"items_total": int(counts[0] or 0),
		"items_done": int(counts[1] or 0),
		"items_revoke": int(counts[2] or 0),
		"items_unassigned": int(counts[3] or 0),
	}
	frappe.db.set_value("Access Review", name, values, update_modified=False)
	return values


def can_decide(item) -> bool:
	from access_registry.permissions import ADMINS, ROLE_MANAGER, has_any

	return item.reviewer_user == frappe.session.user or has_any(*ADMINS, ROLE_MANAGER)


def decide(item_name: str, decision: str, comment: str | None = None) -> dict:
	if decision not in DECISIONS:
		frappe.throw(_("Решение: «Оставить» или «Отозвать»"))
	item = frappe.get_doc("Access Review Item", item_name)
	if not can_decide(item):
		raise frappe.PermissionError(_("Это не ваше задание"))
	if frappe.db.get_value("Access Review", item.access_review, "status") != "Идёт":
		frappe.throw(_("Пересмотр не идёт: решения не принимаются"))
	item.decision = decision
	item.comment = (comment or "").strip() or item.comment
	item.decided_by = frappe.session.user
	item.decided_on = now_datetime()
	item.save(ignore_permissions=True)
	refresh(item.access_review)
	return {"name": item.name, "decision": item.decision}


def finish_review(name: str) -> dict:
	review = frappe.get_doc("Access Review", name)
	if review.status != "Идёт":
		frappe.throw(_("Пересмотр не идёт"))
	review.status = "Завершён"
	review.finished_on = now_datetime()
	review.save(ignore_permissions=True)
	return refresh(name)


def my_items(user: str | None = None) -> list[dict]:
	user = user or frappe.session.user
	rows = frappe.db.sql(
		"""select i.name, i.access_review, r.title as review_title, r.due_date, r.description as review_description,
			i.person, i.full_name, i.person_status, i.system, i.entitlement, i.access_title, i.risk, i.evidence,
			i.decision, i.comment
		from `tabAccess Review Item` i join `tabAccess Review` r on r.name = i.access_review
		where i.reviewer_user = %s and r.status = 'Идёт'
		order by r.creation, i.full_name, i.system, i.access_title""",
		user,
		as_dict=True,
	)
	places = main_places(list({r.person for r in rows}))
	for r in rows:
		place = places.get(r.person) or {}
		r.position = place.get("position_title")
		r.department = place.get("department_title")
		r.due_date = str(r.due_date) if r.due_date else None
	return rows


def pending_count(user: str | None = None) -> int:
	return frappe.db.sql(
		"""select count(*) from `tabAccess Review Item` i join `tabAccess Review` r on r.name = i.access_review
		where i.reviewer_user = %s and r.status = 'Идёт' and ifnull(i.decision, '') = ''""",
		user or frappe.session.user,
	)[0][0]


def results(name: str) -> list[dict]:
	rows = frappe.get_all(
		"Access Review Item",
		filters={"access_review": name},
		fields=["name", "person", "full_name", "person_status", "system", "entitlement", "access_title", "risk",
		        "evidence", "reviewer_user", "decision", "comment", "decided_by", "decided_on"],
		order_by="full_name, system, access_title",
		limit_page_length=0,
	)
	names = defaultdict(str)
	for u in frappe.get_all("User", filters={"name": ["in", list({r.reviewer_user for r in rows if r.reviewer_user}) or [""]]},
	                        fields=["name", "full_name"]):
		names[u.name] = u.full_name or u.name
	for r in rows:
		r.reviewer_name = names.get(r.reviewer_user) or r.reviewer_user or ""
		r.decided_on = str(r.decided_on) if r.decided_on else None
	return rows
