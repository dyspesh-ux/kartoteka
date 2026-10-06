"""Equipment for the management: the fleet, its value and age, movements and what needs attention.

Computed on request from the Snipe-IT mirror (IT Asset, IT Asset Event) and the HR data (where an
employee works). Assets deleted in Snipe-IT (``missing_in_source``) are left out.
"""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime

from access_registry.access_catalog.access_report import main_places
from access_registry.it_assets.orgs import other_org_assets

PERIODS = (30, 90, 365)
STATUS_TITLES = {
	"deployable": "Можно выдать",
	"pending": "Ожидает",
	"undeployable": "Нельзя выдать (ремонт, неисправна)",
	"archived": "Списана, в архиве",
	"": "Статус не указан",
}
AGES = ((1, "до 1 года"), (3, "1–3 года"), (5, "3–5 лет"), (None, "больше 5 лет"))
SOON_DAYS = 90
ASSET_FIELDS = [
	"name",
	"asset_name",
	"asset_tag",
	"category",
	"model",
	"status_label",
	"status_type",
	"assigned_type",
	"assigned_name",
	"person",
	"purchase_date",
	"purchase_cost",
	"warranty_expires",
	"eol",
	"expected_checkin",
	"next_audit_date",
	"company",
	"location",
]


def snapshot(days: int = 90) -> dict:
	days = cint(days) if cint(days) in PERIODS else 90
	now = now_datetime()
	today = now.date()
	start = datetime.combine(today - timedelta(days=days - 1), time.min)
	assets = frappe.get_all("IT Asset", filters={"missing_in_source": 0}, fields=ASSET_FIELDS)
	for a in assets:
		for key in ("purchase_date", "warranty_expires", "eol", "expected_checkin", "next_audit_date"):
			a[key] = getdate(a[key]) if a[key] else None
		a.purchase_cost = flt(a.purchase_cost)
		a.status_type = a.status_type or ""
	active = [a for a in assets if a.status_type != "archived"]
	persons = sorted({a.person for a in active if a.person})
	status_of = dict(
		frappe.get_all(
			"Person", filters={"name": ["in", persons or [""]]}, fields=["name", "status"], as_list=True
		)
	)
	places = main_places(persons)
	issued = [a for a in active if a.assigned_type]
	to_people = [a for a in issued if a.person]
	in_stock = [a for a in active if not a.assigned_type and a.status_type == "deployable"]
	broken = [a for a in active if a.status_type == "undeployable"]

	attention = []

	def flag(asset, issue, tone="amber"):
		attention.append((asset, issue, tone))

	for a in active:
		if a.person and status_of.get(a.person) not in (None, "Работает"):
			flag(a, _("у неработающего сотрудника"), "red")
		if a.assigned_type and a.expected_checkin and a.expected_checkin < today:
			flag(a, _("просрочен возврат"), "red")
		if a.next_audit_date and a.next_audit_date < today:
			flag(a, _("просрочен аудит"))
		if a.eol and a.eol < today:
			flag(a, _("закончился срок службы"))
		elif a.warranty_expires and today <= a.warranty_expires <= add_days(today, SOON_DAYS):
			flag(a, _("гарантия заканчивается"), "")

	other_org, unmatched = other_org_assets(active)
	for a in other_org:
		flag(a, _("куплена на другую организацию"), "red")
	flows = defaultdict(lambda: {"count": 0, "cost": 0.0})
	for a in other_org:
		f = flows[(a["company_org"], a["person_orgs"])]
		f["count"] += 1
		f["cost"] += a.purchase_cost

	purchased = [a for a in assets if a.purchase_date and a.purchase_date >= add_days(today, -365)]
	kpis = {
		"total": len(active),
		"cost": round(sum(a.purchase_cost for a in active), 2),
		"issued": len(issued),
		"to_people": len(to_people),
		"in_stock": len(in_stock),
		"broken": len(broken),
		"archived": len(assets) - len(active),
		"at_dismissed": sum(1 for _a, issue, _t in attention if issue == _("у неработающего сотрудника")),
		"overdue_return": sum(1 for _a, issue, _t in attention if issue == _("просрочен возврат")),
		"overdue_audit": sum(1 for _a, issue, _t in attention if issue == _("просрочен аудит")),
		"eol": sum(1 for a in active if a.eol and a.eol < today),
		"warranty_soon": sum(1 for _a, issue, _t in attention if issue == _("гарантия заканчивается")),
		"purchased_year": len(purchased),
		"purchased_year_cost": round(sum(a.purchase_cost for a in purchased), 2),
		"other_org": len(other_org),
		"other_org_cost": round(sum(a.purchase_cost for a in other_org), 2),
		"working_people": len({a.person for a in to_people if status_of.get(a.person) == "Работает"}),
	}

	categories = defaultdict(lambda: {"total": 0, "issued": 0, "stock": 0, "broken": 0, "cost": 0.0})
	for a in active:
		c = categories[a.category or _("Без категории")]
		c["total"] += 1
		c["cost"] += a.purchase_cost
		c["issued"] += 1 if a.assigned_type else 0
		c["stock"] += 1 if not a.assigned_type and a.status_type == "deployable" else 0
		c["broken"] += 1 if a.status_type == "undeployable" else 0

	statuses = Counter(a.status_type for a in assets)

	ages = Counter()
	for a in active:
		if not a.purchase_date:
			ages[_("дата покупки не указана")] += 1
			continue
		years = (today - a.purchase_date).days / 365.25
		ages[next(label for limit, label in AGES if limit is None or years < limit)] += 1

	departments = defaultdict(lambda: {"count": 0, "cost": 0.0, "people": set()})
	for a in to_people:
		place = places.get(a.person) or {}
		key = (
			place.get("organization_title") or "",
			place.get("department_title") or _("Подразделение не указано"),
		)
		d = departments[key]
		d["count"] += 1
		d["cost"] += a.purchase_cost
		d["people"].add(a.person)

	events = frappe.get_all(
		"IT Asset Event",
		filters={"event_date": [">=", start], "action_type": ["in", ["checkout", "checkin from"]]},
		fields=["event_date", "action_type"],
	)
	by_day = defaultdict(Counter)
	for e in events:
		by_day[getdate(e.event_date)][e.action_type] += 1
	trend = []
	for k in range(days):
		day = (start + timedelta(days=k)).date()
		trend.append(
			{"date": str(day), "checkout": by_day[day]["checkout"], "checkin": by_day[day]["checkin from"]}
		)

	order = {"red": 0, "amber": 1, "": 2}
	attention.sort(key=lambda x: (order[x[2]], x[1], x[0].asset_name or ""))
	names = dict(
		frappe.get_all(
			"Person", filters={"name": ["in", persons or [""]]}, fields=["name", "full_name"], as_list=True
		)
	)
	return {
		"generated": str(now),
		"last_sync": _last_sync(),
		"periods": PERIODS,
		"days": days,
		"kpis": kpis,
		"categories": sorted(
			({"category": k, **v, "cost": round(v["cost"], 2)} for k, v in categories.items()),
			key=lambda r: (-r["total"], r["category"]),
		),
		"statuses": [
			{"label": STATUS_TITLES.get(code, code), "value": statuses.get(code, 0)}
			for code in ("deployable", "pending", "undeployable", "archived", "")
			if statuses.get(code)
		],
		"ages": [
			{"label": label, "value": ages.get(label, 0)}
			for label in [label for _l, label in AGES] + [_("дата покупки не указана")]
			if ages.get(label) or label != _("дата покупки не указана")
		],
		"departments": sorted(
			(
				{
					"organization": org,
					"department": dep,
					"count": v["count"],
					"people": len(v["people"]),
					"cost": round(v["cost"], 2),
				}
				for (org, dep), v in departments.items()
			),
			key=lambda r: (-r["count"], r["department"]),
		),
		"trend": trend,
		"other_org": sorted(
			(
				{"company_org": k[0], "person_orgs": k[1], "count": v["count"], "cost": round(v["cost"], 2)}
				for k, v in flows.items()
			),
			key=lambda r: (-r["count"], r["company_org"]),
		),
		"unmatched_companies": [
			{"company": c, "count": n} for c, n in sorted(unmatched.items(), key=lambda x: (-x[1], x[0]))
		],
		"movements": {
			"checkout": sum(t["checkout"] for t in trend),
			"checkin": sum(t["checkin"] for t in trend),
		},
		"attention": [
			{
				"issue": issue,
				"tone": tone,
				"asset": a.name,
				"asset_name": a.asset_name,
				"asset_tag": a.asset_tag,
				"category": a.category,
				"person": a.person,
				"holder": names.get(a.person) or a.assigned_name,
				"date": _issue_date(a, issue),
				"note": _("куплена на {0}, сотрудник — {1}").format(
					a.get("company_org"), a.get("person_orgs")
				)
				if issue == _("куплена на другую организацию")
				else None,
			}
			for a, issue, tone in attention
		],
	}


def _issue_date(asset, issue):
	"""The date the issue is about: return due, audit due, end of life, end of warranty."""
	value = {
		_("просрочен возврат"): asset.expected_checkin,
		_("просрочен аудит"): asset.next_audit_date,
		_("закончился срок службы"): asset.eol,
		_("гарантия заканчивается"): asset.warranty_expires,
	}.get(issue)
	return str(value) if value else None


def _last_sync():
	value = frappe.db.get_value("Snipe-IT Server", {"enabled": 1}, "max(last_sync)")
	return str(value) if value else None
