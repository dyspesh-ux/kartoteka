"""Recruiting snapshot for planning equipment purchases («Подбор и закупка»).

From the recruiting smart process (``purpose`` = «Подбор персонала»): how many vacancies of which
position, in which organizations, at which stages; when people are expected; what the workplace
needs. Equipment need of a vacancy comes from:
- the request itself — the field «Оборудование для рабочего места» names Snipe-IT categories
  («ноутбук», «2 монитора»); a category is recognised by the start of its name and by the synonyms
  on the process card;
- otherwise the typical kit of the position — what most people working in that position have in
  Snipe-IT now (a category held by at least half of them).
The need is set against the stock (deployable, not handed out) — the difference is what to buy.

Vacancies before the stage «сотрудник оформлен» are «в наборе» and planned; later ones are being
hired (their equipment is usually issued or requested already) and only counted.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import median

import frappe
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime

from access_registry.bitrix24.smart_items import CANCELLED, DONE, HIRING, OPEN

PERIODS = (30, 90, 365)
STALE_DAYS = 30
KIT_SHARE = 0.5  # a category is in the typical kit of a position when at least half the holders have it
FIELDS = [
	"name",
	"item_id",
	"title",
	"url",
	"state",
	"stage_id",
	"stage_name",
	"position",
	"department",
	"organization",
	"office",
	"level",
	"start_date",
	"equipment_note",
	"services",
	"remote",
	"mobile",
	"created_at",
	"moved_at",
	"closed_at",
	"assigned_name",
]
FILL_FIELDS = {
	"position": "Должность",
	"department": "Подразделение",
	"organization": "Организация",
	"level": "Уровень",
	"office": "Офис",
	"equipment_note": "Оборудование",
	"start_date": "Дата выхода",
	"services": "Сервисы",
}


def processes() -> list:
	return frappe.get_all(
		"B24 Smart Process",
		filters={"purpose": HIRING},
		fields=["name", "title", "enabled", "last_sync", "last_status", "open_count"],
		order_by="title",
	)


def norm(text) -> str:
	return re.sub(r"[^\w]+", " ", str(text or "").lower().replace("ё", "е")).strip()


# --------------------------------------------------------------------------- equipment


def category_patterns(categories, synonyms_text) -> dict:
	"""{category: [regex]}: the start of each word of the category name and the synonyms."""
	synonyms = defaultdict(list)
	for line in str(synonyms_text or "").splitlines():
		if ":" in line:
			name, words = line.split(":", 1)
			synonyms[norm(name)] += [w.strip() for w in words.split(",") if w.strip()]
	result = {}
	for category in categories:
		words = [w for w in norm(category).split() if len(w) >= 2]
		stems = [w[:5] if len(w) > 5 else w for w in words[:1]]  # «Ноутбуки» → «ноутб», «МФУ» → «мфу»
		stems += [norm(s) for s in synonyms.get(norm(category), [])]
		patterns = []
		for stem in dict.fromkeys(stems):
			if not stem:
				continue
			body = re.escape(stem) + (r"\w*" if len(stem) >= 4 else r"\b")
			patterns.append(re.compile(r"(?:(\d+)\s*(?:шт\.?\s*)?)?\b" + body, re.I))
		if patterns:
			result[category] = patterns
	return result


def kit_from_text(text, patterns) -> dict:
	"""{category: quantity} named in the free text of a request."""
	text = norm(text) if text else ""
	if not text:
		return {}
	kit = {}
	for category, regexes in patterns.items():
		for regex in regexes:
			m = regex.search(text)
			if m:
				kit[category] = max(kit.get(category, 0), cint(m.group(1)) or 1)
				break
	return kit


def typical_kits(positions) -> dict:
	"""{normalized position: {category: quantity}} from the assets of the people working in it."""
	wanted = {norm(p) for p in positions if p}
	if not wanted:
		return {}
	holders = defaultdict(set)
	for person, title in frappe.db.sql(
		"""select e.person, pos.title from `tabEmployment` e join `tabHR Position` pos on pos.name = e.position
		where e.status in ('Работает', 'Увольняется')"""
	):
		key = norm(title)
		if key in wanted:
			holders[key].add(person)
	people = {p for ps in holders.values() for p in ps}
	owned = defaultdict(Counter)
	for a in frappe.get_all(
		"IT Asset",
		filters={
			"person": ["in", list(people) or [""]],
			"missing_in_source": 0,
			"status_type": ["!=", "archived"],
		},
		fields=["person", "category"],
	):
		if a.category:
			owned[a.person][a.category] += 1
	kits = {}
	for key, persons in holders.items():
		kit = {}
		categories = {c for p in persons for c in owned[p]}
		for category in categories:
			counts = [owned[p][category] for p in persons if owned[p][category]]
			if len(counts) / len(persons) >= KIT_SHARE:
				kit[category] = max(1, round(median(counts)))
		kits[key] = {"kit": kit, "holders": len(persons)}
	return kits


def stock() -> Counter:
	return Counter(
		a.category
		for a in frappe.get_all(
			"IT Asset",
			filters={
				"missing_in_source": 0,
				"status_type": "deployable",
				"assigned_type": ["in", ["", None]],
			},
			fields=["category"],
		)
		if a.category
	)


# --------------------------------------------------------------------------- snapshot


def snapshot(process: str | None = None, days: int = 90) -> dict:
	days = cint(days) if cint(days) in PERIODS else 90
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

	order = [s.stage_id for s in doc.stages if s.state == OPEN]
	hired_at = order.index(doc.hired_stage) if doc.hired_stage in order else len(order)
	recruiting_stages = set(order[:hired_at])

	items = frappe.get_all("B24 Smart Item", filters={"smart_process": doc.name}, fields=FIELDS)
	for i in items:
		i.created_at = get_datetime(i.created_at) if i.created_at else None
		i.moved_at = get_datetime(i.moved_at) if i.moved_at else None
		i.closed_at = get_datetime(i.closed_at) if i.closed_at else None
		i.start_date = getdate(i.start_date) if i.start_date else None
	open_items = [i for i in items if i.state == OPEN]
	recruiting = [i for i in open_items if i.stage_id in recruiting_stages or i.stage_id not in order]
	onboarding = [i for i in open_items if i not in recruiting]
	hired = [i for i in items if i.state == DONE and i.closed_at and i.closed_at >= start]
	cancelled = [i for i in items if i.state == CANCELLED and i.closed_at and i.closed_at >= start]
	hire_days = [
		(i.closed_at - i.created_at).days
		for i in items
		if i.state == DONE and i.created_at and i.closed_at and i.closed_at >= now - timedelta(days=365)
	]

	# vacancies by position and organization, with their stages
	groups = {}
	for i in recruiting:
		key = (i.position or "", i.organization or "")
		g = groups.setdefault(
			key,
			{
				"position": i.position,
				"organization": i.organization,
				"count": 0,
				"stages": Counter(),
				"departments": set(),
				"start": None,
			},
		)
		g["count"] += 1
		g["stages"][i.stage_name] += 1
		if i.department:
			g["departments"].add(i.department)
		if i.start_date and (not g["start"] or i.start_date < g["start"]):
			g["start"] = i.start_date
	vacancies = sorted(
		(
			{
				**g,
				"stages": [
					{"stage": k, "count": v}
					for k, v in sorted(g["stages"].items(), key=lambda x: order_of(order, doc, x[0]))
				],
				"departments": sorted(g["departments"]),
				"start": str(g["start"]) if g["start"] else None,
			}
			for g in groups.values()
		),
		key=lambda g: (-g["count"], g["position"] or "", g["organization"] or ""),
	)

	stage_counts = Counter(i.stage_id for i in open_items)
	funnel = [
		{
			"stage": s.stage_name,
			"count": stage_counts.get(s.stage_id, 0),
			"hired": s.stage_id not in recruiting_stages,
		}
		for s in doc.stages
		if s.state == OPEN
	]

	# equipment: need of the vacancies in recruiting, against the stock
	categories = sorted(
		set(frappe.get_all("IT Asset", filters={"missing_in_source": 0}, pluck="category", distinct=True))
		- {None, ""}
	)
	patterns = category_patterns(categories, doc.equipment_synonyms)
	kits = typical_kits([i.position for i in recruiting])
	need = defaultdict(lambda: {"request": 0, "position": 0})
	plan_rows, undetermined = [], 0
	for i in recruiting:
		kit, source = kit_from_text(i.equipment_note, patterns), "по заявке"
		if not kit:
			k = kits.get(norm(i.position))
			kit, source = (k["kit"], "по должности") if k and k["kit"] else ({}, "не определено")
		if not kit:
			undetermined += 1
		for category, qty in kit.items():
			need[category]["request" if source == "по заявке" else "position"] += qty
		plan_rows.append(
			{
				"item_id": i.item_id,
				"url": i.url,
				"position": i.position,
				"organization": i.organization,
				"department": i.department,
				"stage": i.stage_name,
				"level": i.level,
				"start_date": str(i.start_date) if i.start_date else None,
				"equipment_note": i.equipment_note,
				"kit": ", ".join(f"{c} × {q}" if q > 1 else c for c, q in sorted(kit.items())),
				"source": source,
				"remote": i.remote,
				"mobile": i.mobile,
			}
		)
	on_hand = stock()
	equipment = []
	for category in sorted(set(need) | {c for c in on_hand if c in need}):
		total = need[category]["request"] + need[category]["position"]
		equipment.append(
			{
				"category": category,
				"request": need[category]["request"],
				"position": need[category]["position"],
				"need": total,
				"stock": on_hand.get(category, 0),
				"buy": max(0, total - on_hand.get(category, 0)),
			}
		)
	equipment.sort(key=lambda r: (-r["buy"], -r["need"], r["category"]))

	services = Counter()
	for i in recruiting:
		for s in (i.services or "").split(","):
			if s.strip():
				services[s.strip()] += 1

	fill = [
		{
			"field": title,
			"share": round(100 * sum(1 for i in open_items if i.get(key)) / len(open_items))
			if open_items
			else 0,
		}
		for key, title in FILL_FIELDS.items()
	]

	return {
		"processes": [{"name": p.name, "title": p.title or p.name} for p in procs],
		"process": {
			"name": proc.name,
			"title": proc.title or proc.name,
			"last_sync": str(proc.last_sync) if proc.last_sync else None,
			"last_status": proc.last_status,
			"hired_stage": next((s.stage_name for s in doc.stages if s.stage_id == doc.hired_stage), None),
		},
		"generated": str(now),
		"periods": PERIODS,
		"days": days,
		"kpis": {
			"recruiting": len(recruiting),
			"onboarding": len(onboarding),
			"positions": len({i.position for i in recruiting if i.position}),
			"organizations": len({i.organization for i in recruiting if i.organization}),
			"leaders": sum(1 for i in recruiting if re.search(r"руковод", i.level or "", re.I)),
			"created": sum(1 for i in items if i.created_at and i.created_at >= start),
			"hired": len(hired),
			"cancelled": len(cancelled),
			"median_days": round(median(hire_days)) if hire_days else None,
			"stale": sum(
				1 for i in recruiting if i.moved_at and i.moved_at < now - timedelta(days=STALE_DAYS)
			),
			"start_soon": sum(
				1 for i in open_items if i.start_date and today <= i.start_date <= add_days(today, 30)
			),
			"remote": sum(1 for i in recruiting if i.remote),
			"mobile": sum(1 for i in recruiting if i.mobile),
			"undetermined": undetermined,
			"to_buy": sum(r["buy"] for r in equipment),
		},
		"vacancies": vacancies,
		"funnel": funnel,
		"organizations": [
			{"organization": k or "не указана", "count": v}
			for k, v in Counter(i.organization for i in recruiting).most_common()
		],
		"equipment": equipment,
		"plan": sorted(plan_rows, key=lambda r: (r["start_date"] or "9999", r["position"] or "")),
		"services": [{"service": k, "count": v} for k, v in services.most_common()],
		"fill": fill,
		"categories": categories,
	}


def order_of(order, doc, stage_name):
	names = [s.stage_name for s in doc.stages if s.stage_id in order]
	return names.index(stage_name) if stage_name in names else len(names)
