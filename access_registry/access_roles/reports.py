"""Reports of the role model."""

from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from access_registry.access_catalog.access_report import main_places
from access_registry.access_roles import engine


def col(fieldname, label, fieldtype="Data", width=160, options=None):
	c = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
	if options:
		c["options"] = options
	return c


def reconciliation(filters=None):
	"""Сверка доступа: что положено по ролям и процессам и что есть в 1С, AD и Битрикс24."""
	filters = frappe._dict(filters or {})
	model = engine.RoleModel()
	persons = None
	if filters.get("person"):
		persons = {filters.person}
	elif filters.get("access_role"):
		persons = set(model.members_of_role(filters.access_role))
	rows = engine.reconcile(persons, model)
	statuses = set(filters.get("statuses") or [])
	if isinstance(filters.get("statuses"), str):
		statuses = {s.strip() for s in filters.statuses.split(",") if s.strip()}
	if filters.get("status"):
		statuses = {filters.status}
	data = []
	for row in rows:
		if statuses and row["status"] not in statuses:
			continue
		if not statuses and row["status"] == engine.OK and not cint(filters.get("show_ok")):
			continue
		if filters.get("system") and row["system"] != filters.system:
			continue
		if cint(filters.get("only_privileged")) and not row["privileged"]:
			continue
		data.append(row)
	columns = [
		col("full_name", _("Сотрудник"), width=220),
		col("person_status", _("Статус"), width=100),
		col("status", _("Итог сверки"), width=150),
		col("title", _("Право доступа"), width=260),
		col("system", _("Система"), width=110),
		col("risk", _("Риск"), width=90),
		col("expected_by", _("Положено по"), width=260),
		col("evidence", _("Где есть"), width=200),
		col("exception", _("Исключение"), "Link", 120, "Access Exception"),
		col("entitlement", _("Карточка права"), "Link", 120, "Entitlement"),
		col("person", _("Карточка сотрудника"), "Link", 150, "Person"),
	]
	return columns, data


def sod_conflicts(filters=None):
	"""Конфликты полномочий: у сотрудника есть права с обеих сторон правила SoD."""
	filters = frappe._dict(filters or {})
	data = engine.sod_conflicts({filters.person} if filters.get("person") else None)
	if filters.get("rule"):
		data = [r for r in data if r["rule"] == filters.rule]
	columns = [
		col("title", _("Конфликт"), width=240),
		col("severity", _("Критичность"), width=110),
		col("full_name", _("Сотрудник"), width=220),
		col("person_status", _("Статус"), width=100),
		col("side_a", _("Первая сторона"), width=260),
		col("side_b", _("Вторая сторона"), width=260),
		col("rule", _("Правило"), "Link", 150, "SoD Rule"),
		col("person", _("Карточка"), "Link", 150, "Person"),
	]
	return columns, data


def role_mining(filters=None):
	"""Подбор ролей: какие доступы уже есть у большинства сотрудников каждой должности."""
	filters = frappe._dict(filters or {})
	threshold = (flt(filters.get("threshold")) or 80) / 100
	rows = engine.mine_roles(cint(filters.get("min_people")) or 3, threshold)
	if cint(filters.get("only_suggested", 1)):
		rows = [r for r in rows if r["suggested"]]
	if filters.get("position"):
		needle = filters.position.lower()
		rows = [r for r in rows if needle in (r["position"] or "").lower()]
	columns = [
		col("position", _("Должность"), width=220),
		col("people", _("Сотрудников"), "Int", 100),
		col("title", _("Доступ"), width=300),
		col("holders", _("Есть у"), "Int", 80),
		col("share", _("Доля, %"), "Float", 90),
		col("suggested", _("В роль"), "Check", 70),
		col("entitlement", _("В каталоге"), "Link", 120, "Entitlement"),
		col("existing_role", _("Роль уже есть"), "Link", 180, "Access Role"),
	]
	return columns, rows


def role_members(filters=None):
	"""Состав ролей доступа: кто получает роль и почему."""
	filters = frappe._dict(filters or {})
	model = engine.RoleModel()
	roles = [filters.access_role] if filters.get("access_role") else list(model.roles)
	persons = defaultdict(dict)
	for person in model.persons:
		for role, reason in model.roles_of(person).items():
			if role in roles:
				persons[person][role] = reason
	places = main_places(list(persons))
	data = []
	for person, items in persons.items():
		info = model.persons[person]
		place = places.get(person, {})
		for role, reason in items.items():
			data.append(
				{
					"access_role": role,
					"person": person,
					"full_name": info.full_name,
					"person_status": info.status,
					"reason": reason,
					"position": place.get("position_title"),
					"department": place.get("department_title"),
					"organization": place.get("organization_title"),
				}
			)
	data.sort(key=lambda r: (r["access_role"], r["full_name"] or ""))
	columns = [
		col("access_role", _("Роль доступа"), "Link", 220, "Access Role"),
		col("full_name", _("Сотрудник"), width=220),
		col("reason", _("Почему"), width=220),
		col("position", _("Должность"), width=180),
		col("department", _("Подразделение"), width=180),
		col("organization", _("Организация"), width=160),
		col("person", _("Карточка"), "Link", 150, "Person"),
	]
	return columns, data


def unmanaged_access(filters=None):
	"""Доступы вне каталога: что есть у сотрудников, но не описано как право доступа."""
	filters = frappe._dict(filters or {})
	_actual, other = engine.actual_entitlements()
	holders = Counter()
	samples = defaultdict(list)
	for person, items in other.items():
		for key in items:
			holders[key] += 1
			if len(samples[key]) < 3:
				samples[key].append(person)
	names = dict(frappe.get_all("Person", fields=["name", "full_name"], as_list=True, limit_page_length=0))
	data = []
	for key, count in holders.most_common():
		if count < (cint(filters.get("min_holders")) or 1):
			continue
		info = engine.describe_key(key)
		if filters.get("system") and info["system"] != filters.system:
			continue
		data.append(
			{
				"key": key,
				"title": info["title"],
				"system": info["system"],
				"holders": count,
				"examples": ", ".join(names.get(p, p) for p in samples[key]),
			}
		)
	columns = [
		col("title", _("Доступ"), width=320),
		col("system", _("Система"), width=120),
		col("holders", _("Есть у сотрудников"), "Int", 130),
		col("examples", _("Например"), width=320),
		col("key", _("Ключ"), width=200),
	]
	return columns, data


def review_results(filters=None):
	"""Результаты пересмотра доступа: решения проверяющих, список на отзыв."""
	from access_registry.access_roles.review import results

	filters = frappe._dict(filters or {})
	if not filters.get("access_review"):
		return [], []
	rows = results(filters.access_review)
	if filters.get("decision") == "Без решения":
		rows = [r for r in rows if not r.decision]
	elif filters.get("decision"):
		rows = [r for r in rows if r.decision == filters.decision]
	if filters.get("reviewer_user"):
		rows = [r for r in rows if r.reviewer_user == filters.reviewer_user]
	columns = [
		col("full_name", _("Сотрудник"), width=220),
		col("person_status", _("Статус"), width=100),
		col("access_title", _("Доступ"), width=260),
		col("system", _("Система"), width=110),
		col("risk", _("Риск"), width=90),
		col("evidence", _("Где есть"), width=180),
		col("reviewer_name", _("Проверяющий"), width=180),
		col("decision", _("Решение"), width=100),
		col("comment", _("Комментарий"), width=220),
		col("decided_on", _("Когда"), "Datetime", 150),
		col("person", _("Карточка"), "Link", 150, "Person"),
	]
	return columns, rows
