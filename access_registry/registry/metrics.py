"""Key figures of the registry for the management dashboard («Руководству») and their history.

Every hour the scheduler stores the figures of the day in «Registry Metric Snapshot» (one record a
day, the last count of the day stays), so the dashboard can show the trend. The figures are totals
only: no names of people or accounts.
"""

import frappe
from frappe.utils import add_days, cint, getdate, now_datetime, today

CACHE_KEY = "access_registry:management_metrics"
SYSTEMS = {"1С": "1c", "AD": "ad", "Битрикс24": "b24"}
PERIODS = (7, 30, 90, 365)


def collect(refresh: bool = True) -> dict:
	"""The figures right now (a few seconds: the role model is reconciled). Without refresh the
	counters of «Обзор» counted in the last 5 minutes are taken."""
	from access_registry.registry.api import dashboard_data

	d = dashboard_data(refresh)
	r = d["reconciliation"]
	m = {
		"people_working": d["people"]["working"],
		"people_total": d["people"]["total"],
		"dismissed_people": d["dismissed_access"]["people"],
		"unlinked": sum(d["unlinked"].values()),
		"model_enabled": int(bool(r["enabled"])),
		"ok": r["ok"],
		"excess": r["excess"] + r["excess_not_working"],
		"excess_not_working": r["excess_not_working"],
		"missing": r["missing"],
		"exceptions": r["exceptions"],
		"privileged": r["privileged"],
		"sod": d["sod"],
		"process_risks": d["processes"]["risks"],
		"suppressed": d["suppressed"],
		"events": d["events"],
		"expiring": d["expiring"],
		"extra_roles": d["quality"]["extra_roles"],
		"b24_admins": d["quality"]["b24_admins"],
	}
	checked = r["ok"] + r["missing"] + r["excess"] + r["excess_not_working"]
	m["compliance"] = round(100 * r["ok"] / checked, 1) if r["enabled"] and checked else None
	for system, code in SYSTEMS.items():
		m[f"dismissed_{code}"] = d["dismissed_access"]["by_system"].get(system, 0)
		m[f"unlinked_{code}"] = d["unlinked"].get(system, 0)
	m.update(_coverage())
	m["review_pending"] = cint(
		frappe.db.sql(
			"""select count(*) from `tabAccess Review Item` i join `tabAccess Review` r on r.name = i.access_review
			where r.status = 'Идёт' and ifnull(i.decision, '') = ''"""
		)[0][0]
	)
	return m


def _coverage() -> dict:
	"""Active accounts of every system and how many of them are linked to an employee."""
	queries = {
		"1c": """select count(*), sum(ifnull(person, '') != '') from `tabIB User`
			where login_allowed = 1 and invalid = 0 and missing_in_source = 0 and service = 0 and is_orphan = 0""",
		"ad": """select count(*), sum(ifnull(person, '') != '') from `tabAD Account`
			where enabled = 1 and missing_in_source = 0""",
		"b24": """select count(*), sum(ifnull(person, '') != '') from `tabB24 User`
			where active = 1 and missing_in_source = 0 and ifnull(user_type, '') in ('', 'employee')""",
	}
	result = {}
	for code, sql in queries.items():
		total, linked = frappe.db.sql(sql)[0]
		result[f"accounts_{code}"] = cint(total)
		result[f"linked_{code}"] = cint(linked)
	return result


def current(refresh: bool = False) -> dict:
	data = None if refresh else frappe.cache().get_value(CACHE_KEY)
	if not data:
		data = {"taken_on": str(now_datetime()), "metrics": collect(refresh)}
		frappe.cache().set_value(CACHE_KEY, data, expires_in_sec=300)
	return data


def save_snapshot(day=None, metrics: dict | None = None) -> str:
	"""Store the figures of the day (the record of the day is overwritten by later counts)."""
	day = str(getdate(day or today()))
	metrics = metrics if metrics is not None else collect()
	name = frappe.db.exists("Registry Metric Snapshot", {"snapshot_date": day})
	doc = (
		frappe.get_doc("Registry Metric Snapshot", name)
		if name
		else frappe.new_doc("Registry Metric Snapshot", snapshot_date=day)
	)
	doc.taken_on = now_datetime()
	doc.metrics = frappe.as_json(metrics)
	doc.save(ignore_permissions=True) if name else doc.insert(ignore_permissions=True)
	return doc.name


def scheduled():
	"""Hourly: the figures of today (the last run of the day is the value of the day)."""
	data = current(refresh=True)
	save_snapshot(metrics=data["metrics"])


def history(days: int) -> list[dict]:
	"""Daily figures for the period, today's live figures last."""
	since = add_days(today(), -days)
	rows = frappe.get_all(
		"Registry Metric Snapshot",
		filters={"snapshot_date": [">=", since], "name": ["!=", today()]},
		fields=["snapshot_date", "metrics"],
		order_by="snapshot_date asc",
		limit_page_length=0,
	)
	result = []
	for row in rows:
		metrics = frappe.parse_json(row.metrics) if row.metrics else {}
		result.append({"date": str(row.snapshot_date), **metrics})
	return result


def sources_health() -> list[dict]:
	from access_registry.registry.api import sources

	return [
		{k: s[k] for k in ("kind", "name", "title", "enabled", "last", "state", "status")}
		for s in sources()
		if s["enabled"]
	]


def reviews_progress() -> list[dict]:
	"""Running campaigns and the ones finished in the last 90 days."""
	rows = frappe.get_all(
		"Access Review",
		filters={"status": ["in", ["Идёт", "Завершён"]]},
		fields=[
			"name",
			"title",
			"status",
			"due_date",
			"items_total",
			"items_done",
			"items_revoke",
			"finished_on",
		],
		order_by="creation desc",
		limit_page_length=20,
	)
	since = getdate(add_days(today(), -90))
	result = []
	for r in rows:
		if r.status != "Идёт" and (not r.finished_on or getdate(r.finished_on) < since):
			continue
		r.due_date = str(r.due_date) if r.due_date else None
		r.finished_on = str(r.finished_on) if r.finished_on else None
		result.append(r)
	return result


def dashboard(days: int = 30, refresh: bool = False) -> dict:
	days = cint(days) if cint(days) in PERIODS else 30
	now = current(refresh)
	points = history(days)
	points.append({"date": today(), **now["metrics"]})
	return {
		"generated": now["taken_on"],
		"days": days,
		"periods": PERIODS,
		"current": now["metrics"],
		"history": points,
		"sources": sources_health(),
		"reviews": reviews_progress(),
	}
