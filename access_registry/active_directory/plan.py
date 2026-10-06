"""AD change plan, stage 1 of provisioning: the registry plans, ИБ approves, a person runs the script.

The registry has no write rights in AD. It compares the AD mirror with the HR data and builds a plan:
- disable accounts of people who no longer work anywhere: every employment (main place and part-time
  jobs) is over, the last one ended at least ``plan_grace_days`` ago, and every employment is in the
  latest ZUP export (an employment missing from the export is a reason to check, not to disable);
- update title, department, company, employeeNumber of working people from their main place of work.

Never planned: accounts not linked to an employee, accounts excluded on the domain card (logins or
OUs), members of protected groups, accounts already in the OU for disabled ones. Too many disables at
once (``plan_max_disable_share``) stop the plan: that is more likely a broken export than a wave of
dismissals.

The plan is executed by ``script.py`` (PowerShell, dry run by default, with a rollback file).
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, getdate, today

from access_registry.access_catalog.access_report import main_places

WORKING = ("Работает", "Увольняется")
DISABLE, UPDATE = "Отключить", "Изменить"
ATTRIBUTES = (
	# AD attribute, flag on AD Domain, field of AD Account, value from the main place of work
	("title", "plan_attr_title", "title", "position_title"),
	("department", "plan_attr_department", "department", "department_title"),
	("company", "plan_attr_company", "company", "organization_title"),
	("employeeNumber", "plan_attr_employee_number", "employee_number", "tab_number"),
)


class PlanStopped(frappe.ValidationError):
	pass


def _norm(value) -> str:
	return " ".join(str(value or "").split())


def _lines(text) -> list[str]:
	return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _dn_under(dn: str, ou: str) -> bool:
	dn, ou = (dn or "").lower().replace(" ", ""), (ou or "").lower().replace(" ", "")
	return bool(ou) and dn.endswith("," + ou)


def build(domain: str, today_date=None) -> frappe._dict:
	"""Items of the plan (not saved): {items, skipped, disable_count, update_count, data_as_of}."""
	d = frappe.get_doc("AD Domain", domain)
	today_date = getdate(today_date or today())
	excluded = _lines(d.plan_exclude)
	excluded_logins = {x.lower() for x in excluded if "=" not in x}
	excluded_ous = [x for x in excluded if "=" in x]
	protected = {x.lower() for x in _lines(d.plan_protected_groups)}

	accounts = frappe.get_all(
		"AD Account",
		filters={"domain": domain, "missing_in_source": 0, "enabled": 1},
		fields=[
			"name",
			"sam_account_name",
			"display_name",
			"person",
			"object_guid",
			"distinguished_name",
			"title",
			"department",
			"company",
			"employee_number",
		],
	)
	groups = defaultdict(set)
	for row in frappe.get_all(
		"AD Account Group",
		filters={"parenttype": "AD Account", "parent": ["in", [a.name for a in accounts] or [""]]},
		fields=["parent", "group_name"],
	):
		groups[row.parent].add((row.group_name or "").lower())

	persons = sorted({a.person for a in accounts if a.person})
	employments = defaultdict(list)
	for e in frappe.get_all(
		"Employment",
		filters={"person": ["in", persons or [""]]},
		fields=[
			"person",
			"status",
			"missing",
			"termination_date",
			"organization",
			"tab_number",
			"employment_kind",
		],
	):
		employments[e.person].append(e)
	org_titles = dict(frappe.get_all("HR Organization", fields=["name", "title"], as_list=True))
	places = main_places(persons)
	tab_numbers = _main_tab_numbers(persons)

	items, skipped = [], []

	def skip(a, why):
		skipped.append(f"{a.sam_account_name or a.display_name}: {why}")

	for a in sorted(accounts, key=lambda x: x.display_name or x.sam_account_name or ""):
		login = (a.sam_account_name or "").lower()
		if not a.person:
			continue  # accounts without an employee are only shown in «Контроль»
		if login in excluded_logins or any(_dn_under(a.distinguished_name, ou) for ou in excluded_ous):
			skip(a, _("в списке «Не трогать»"))
			continue
		if d.plan_disabled_ou and _dn_under(a.distinguished_name, d.plan_disabled_ou):
			skip(a, _("уже в OU для отключённых, но включена — проверьте вручную"))
			continue
		hit = groups[a.name] & protected
		if hit:
			skip(a, _("участник защищённой группы {0}").format(", ".join(sorted(hit))))
			continue
		jobs = employments.get(a.person) or []
		base = {
			"account": a.name,
			"sam_account_name": a.sam_account_name,
			"display_name": a.display_name,
			"person": a.person,
			"object_guid": a.object_guid,
			"distinguished_name": a.distinguished_name,
		}
		working = [e for e in jobs if e.status in WORKING]
		if working:
			place = places.get(a.person) or {}
			values = {**place, "tab_number": tab_numbers.get(a.person)}
			for attribute, flag, field, source in ATTRIBUTES:
				if not d.get(flag):
					continue
				new = _norm(values.get(source))
				if new and new.lower() != _norm(a.get(field)).lower():
					items.append(
						{
							**base,
							"action": UPDATE,
							"attribute": attribute,
							"before": _norm(a.get(field)),
							"after": new,
							"reason": _("по ЗУП: основное место работы"),
						}
					)
			continue
		if not jobs:
			continue
		if any(e.missing for e in jobs):
			skip(a, _("место работы пропало из выгрузки ЗУП — сначала проверьте кадры"))
			continue
		ends = [getdate(e.termination_date) for e in jobs if e.termination_date]
		if not ends or len(ends) < len(jobs):
			skip(a, _("нет даты увольнения в ЗУП"))
			continue
		last = max(ends)
		if add_days(last, cint(d.plan_grace_days)) > today_date:
			continue  # dismissed recently: the next plan will take it
		where = ", ".join(sorted({org_titles.get(e.organization) or e.organization or "" for e in jobs}))
		items.append(
			{
				**base,
				"action": DISABLE,
				"attribute": "",
				"before": _("включена"),
				"after": _("отключена")
				+ (_(", перенесена в {0}").format(d.plan_disabled_ou) if d.plan_disabled_ou else "")
				+ (_(", группы сняты") if d.plan_remove_groups else ""),
				"reason": _("уволен(а) {0}: {1}; других мест работы нет").format(
					formatdate(last, "dd.MM.yyyy"), where
				),
			}
		)

	disables = [i for i in items if i["action"] == DISABLE]
	linked = sum(1 for a in accounts if a.person)
	share = flt(d.plan_max_disable_share) or 5
	if linked and disables and 100 * len(disables) / linked > share:
		raise PlanStopped(
			_(
				"Отключить нужно {0} из {1} включённых учёток сотрудников — больше {2}%. Это похоже на ошибку "
				"выгрузки ЗУП, план не собран. Проверьте кадры; если увольнения настоящие — поднимите порог "
				"в карточке домена."
			).format(len(disables), linked, share)
		)
	limit = cint(d.plan_max_items) or 100
	if len(items) > limit:
		skipped.append(
			_("ещё {0} изменений не вошли: лимит {1} на план — войдут в следующий").format(
				len(items) - limit, limit
			)
		)
		# disabling dismissed people first: that is the risk
		items = sorted(items, key=lambda i: i["action"] != DISABLE)[:limit]
	return frappe._dict(
		items=items,
		skipped=skipped,
		disable_count=sum(1 for i in items if i["action"] == DISABLE),
		update_count=sum(1 for i in items if i["action"] == UPDATE),
		data_as_of=d.last_sync,
	)


def _main_tab_numbers(persons) -> dict:
	"""Tab number of the main place of work (the employment main_places picks)."""
	places = main_places(persons)
	result = {}
	for e in frappe.get_all(
		"Employment",
		filters={"person": ["in", list(persons) or [""]], "status": ["in", WORKING]},
		fields=["person", "organization", "tab_number", "hire_date"],
	):
		place = places.get(e.person) or {}
		if e.organization == place.get("organization") and str(e.hire_date or "") == str(
			place.get("hire_date") or ""
		):
			result[e.person] = e.tab_number
	return result


def create(domain: str) -> str:
	"""Builds the plan and saves it as a draft «AD Change Plan»."""
	plan = build(domain)
	if not plan["items"]:
		frappe.throw(_("Менять нечего: AD совпадает с кадровыми данными.") + _skipped_note(plan))
	doc = frappe.get_doc(
		{
			"doctype": "AD Change Plan",
			"domain": domain,
			"status": "Черновик",
			"items": plan["items"],
			"skipped": "\n".join(plan["skipped"]),
			"disable_count": plan["disable_count"],
			"update_count": plan["update_count"],
			"data_as_of": plan["data_as_of"],
		}
	).insert(ignore_permissions=True)
	return doc.name


def _skipped_note(plan) -> str:
	return (" " + _("Пропущено: {0}.").format(len(plan["skipped"]))) if plan["skipped"] else ""


def item_state(item, account) -> str:
	"""After the script: what the latest AD load shows for one item."""
	if not account or account.missing_in_source:
		return "учётки нет в AD"
	if item.action == DISABLE:
		return "выполнено" if not account.enabled else "не выполнено"
	field = next((f for attr, _flag, f, _src in ATTRIBUTES if attr == item.attribute), None)
	current = _norm(account.get(field)) if field else ""
	if current.lower() == _norm(item.after).lower():
		return "выполнено"
	if current.lower() == _norm(item.before).lower():
		return "не выполнено"
	return "изменено иначе"


def states(plan_doc) -> dict:
	names = [i.account for i in plan_doc.items if i.account]
	accounts = {
		a.name: a
		for a in frappe.get_all(
			"AD Account",
			filters={"name": ["in", names or [""]]},
			fields=[
				"name",
				"enabled",
				"missing_in_source",
				"title",
				"department",
				"company",
				"employee_number",
			],
		)
	}
	return {i.name: item_state(i, accounts.get(i.account)) for i in plan_doc.items}
