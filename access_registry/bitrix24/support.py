"""Snapshot of a helpdesk kept as a Bitrix24 smart process: what is open, overdue, who carries it.

Computed from the mirror «B24 Smart Item» (``smart_items.py``) on request; a few thousand items
are counted in Python.
"""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import median

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime

from access_registry.bitrix24.smart_items import CANCELLED, DONE, OPEN

PERIODS = (7, 30, 90)
AGES = (
	(1, "до 1 дня"),
	(3, "1–3 дня"),
	(7, "3–7 дней"),
	(30, "1–4 недели"),
	(None, "больше месяца"),
)
FIELDS = [
	"name",
	"item_id",
	"title",
	"url",
	"state",
	"stage_id",
	"stage_name",
	"category",
	"source",
	"created_at",
	"closed_at",
	"deadline",
	"done_date",
	"requester",
	"requester_name",
	"assignee",
	"assigned_name",
]


def processes() -> list:
	"""Helpdesk processes only (a recruiting process has its own section)."""
	return frappe.get_all(
		"B24 Smart Process",
		filters={"purpose": ["!=", "Подбор персонала"]},
		fields=["name", "title", "enabled", "last_sync", "last_status", "open_count"],
		order_by="title",
	)


def snapshot(process: str | None = None, days: int = 30) -> dict:
	days = cint(days) if cint(days) in PERIODS else 30
	procs = processes()
	if not procs:
		return {"processes": [], "periods": PERIODS, "days": days}
	proc = next((p for p in procs if p.name == process), None) or next(
		(p for p in procs if p.enabled), procs[0]
	)
	doc = frappe.get_doc("B24 Smart Process", proc.name)
	now = now_datetime()
	today = now.date()
	start = datetime.combine(today - timedelta(days=days - 1), time.min)
	items = frappe.get_all("B24 Smart Item", filters={"smart_process": proc.name}, fields=FIELDS)
	for i in items:
		i.created_at = get_datetime(i.created_at) if i.created_at else None
		i.closed_at = get_datetime(i.closed_at) if i.closed_at else None
		i.deadline = getdate(i.deadline) if i.deadline else None
	open_items = [i for i in items if i.state == OPEN]
	overdue = [i for i in open_items if i.deadline and i.deadline < today]
	closed = [i for i in items if i.state != OPEN and i.closed_at and i.closed_at >= start]
	done = [i for i in closed if i.state == DONE]
	hours = [
		(i.closed_at - i.created_at).total_seconds() / 3600
		for i in done
		if i.created_at and i.closed_at >= i.created_at
	]
	with_deadline = [i for i in done if i.deadline]
	on_time = [i for i in with_deadline if i.closed_at.date() <= i.deadline]

	kpis = {
		"open": len(open_items),
		"overdue": len(overdue),
		"due_today": sum(1 for i in open_items if i.deadline == today),
		"unassigned": sum(1 for i in open_items if not i.assigned_name),
		"new_today": sum(1 for i in items if i.created_at and i.created_at.date() == today),
		"created": sum(1 for i in items if i.created_at and i.created_at >= start),
		"done": len(done),
		"cancelled": sum(1 for i in closed if i.state == CANCELLED),
		"median_hours": round(median(hours), 1) if hours else None,
		"on_time": round(100 * len(on_time) / len(with_deadline)) if with_deadline else None,
		"with_deadline": len(with_deadline),
	}

	open_by_stage = Counter(i.stage_id for i in open_items)
	stages = [
		{"stage": s.stage_name, "funnel": s.funnel, "value": open_by_stage.pop(s.stage_id, 0)}
		for s in doc.stages
		if s.state == OPEN
	]
	stages += [
		{"stage": next(i.stage_name for i in open_items if i.stage_id == sid), "value": n}
		for sid, n in open_by_stage.items()
	]

	categories = defaultdict(lambda: {"open": 0, "overdue": 0, "created": 0})
	for i in open_items:
		categories[i.category or _("Без категории")]["open"] += 1
	for i in overdue:
		categories[i.category or _("Без категории")]["overdue"] += 1
	for i in items:
		if i.created_at and i.created_at >= start:
			categories[i.category or _("Без категории")]["created"] += 1

	people = defaultdict(lambda: {"open": 0, "overdue": 0, "done": 0, "person": None})
	for i in open_items:
		row = people[i.assigned_name or _("Не назначен")]
		row["open"] += 1
		row["person"] = row["person"] or i.assignee
	for i in overdue:
		people[i.assigned_name or _("Не назначен")]["overdue"] += 1
	for i in done:
		row = people[i.assigned_name or _("Не назначен")]
		row["done"] += 1
		row["person"] = row["person"] or i.assignee

	ages = Counter()
	for i in open_items:
		age = (now - i.created_at).total_seconds() / 86400 if i.created_at else 0
		ages[next(label for limit, label in AGES if limit is None or age < limit)] += 1

	trend = []
	for k in range(days):
		day = (start + timedelta(days=k)).date()
		end = datetime.combine(day, time.max)
		trend.append(
			{
				"date": str(day),
				"created": sum(1 for i in items if i.created_at and i.created_at.date() == day),
				"closed": sum(
					1 for i in items if i.state != OPEN and i.closed_at and i.closed_at.date() == day
				),
				"open": sum(
					1
					for i in items
					if i.created_at
					and i.created_at <= end
					and (i.state == OPEN or (i.closed_at and i.closed_at > end))
				),
			}
		)

	rows = sorted(
		open_items,
		key=lambda i: (not (i.deadline and i.deadline < today), i.created_at or now),
	)
	return {
		"processes": [
			{"name": p.name, "title": p.title or p.name, "open": p.open_count, "enabled": p.enabled}
			for p in procs
		],
		"process": {
			"name": proc.name,
			"title": proc.title or proc.name,
			"last_sync": str(proc.last_sync) if proc.last_sync else None,
			"last_status": proc.last_status,
		},
		"generated": str(now),
		"periods": PERIODS,
		"days": days,
		"kpis": kpis,
		"stages": stages,
		"categories": sorted(
			({"category": k, **v} for k, v in categories.items()),
			key=lambda r: (-r["open"], -r["created"], r["category"]),
		),
		"people": sorted(
			({"name": k, **v} for k, v in people.items()),
			key=lambda r: (-r["open"], -r["overdue"], -r["done"], r["name"]),
		),
		"ages": [{"label": label, "value": ages.get(label, 0)} for _limit, label in AGES],
		"trend": trend,
		"open_items": [
			{
				"item_id": i.item_id,
				"title": i.title,
				"url": i.url,
				"stage": i.stage_name,
				"category": i.category,
				"requester_name": i.requester_name,
				"requester": i.requester,
				"assigned_name": i.assigned_name,
				"assignee": i.assignee,
				"created_at": str(i.created_at) if i.created_at else None,
				"deadline": str(i.deadline) if i.deadline else None,
				"overdue": bool(i.deadline and i.deadline < today),
				"age_days": (today - i.created_at.date()).days if i.created_at else None,
			}
			for i in rows
		],
	}


def person_items(person: str, limit: int = 20) -> dict:
	"""Requests of one employee (who asked) for the person card: the latest and the count of open."""
	rows = frappe.get_all(
		"B24 Smart Item",
		filters={"requester": person},
		fields=[
			"item_id",
			"title",
			"url",
			"state",
			"stage_name",
			"category",
			"created_at",
			"closed_at",
			"deadline",
			"assigned_name",
		],
		order_by="created_at desc",
		limit=limit,
	)
	return {
		"open": frappe.db.count("B24 Smart Item", {"requester": person, "state": OPEN}),
		"total": frappe.db.count("B24 Smart Item", {"requester": person}),
		"items": [
			{k: (str(v) if k in ("created_at", "closed_at", "deadline") and v else v) for k, v in r.items()}
			for r in rows
		],
	}
