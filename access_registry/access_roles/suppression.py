"""Suppressed alerts: an alert of a control list is hidden with a reason, and the journal keeps who and why.

An alert is identified by a key built from the row of its control list (for example the 1C user of an
«account without employee» alert). The key is computed on the server from the current list, so the
journal always describes a real alert. A suppression may have an end date: after it the alert shows
again (the nightly job marks such entries «Истёк срок»).
"""

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, today

ACTIVE = "Погашено"
RESTORED = "Возвращено"
EXPIRED = "Истёк срок"

# control list → (fields of the row that identify the alert, how to name it in the journal)
SUPPRESSIBLE = {
	"dismissed": (("ref_doctype", "ref"), lambda r: f"{r.get('account')} — {r.get('full_name')}"),
	"unlinked": (("ref_doctype", "ref"), lambda r: f"{r.get('system')}: {r.get('account')}"),
	"stale": (("ref_doctype", "ref"), lambda r: f"{r.get('system')}: {r.get('account')}"),
	"privileged": (("person", "full_name", "title"), lambda r: f"{r.get('full_name')}: {r.get('title')}"),
	"sod": (("rule", "person"), lambda r: f"{r.get('full_name')}: {r.get('title')}"),
	"processes": (("process_role",), lambda r: f"{r.get('process_title')}: {r.get('role_name')}"),
	"quality": (("area", "person", "full_name"), lambda r: f"{r.get('full_name')}: {r.get('area')}"),
	"shares": (
		("folder", "issue", "person"),
		lambda r: (
			f"{r.get('share_name')} {r.get('path')}: {r.get('issue')}"
			+ (f" ({r.get('full_name')})" if r.get("full_name") else "")
		),
	),
}


def alert_key(kind: str, row) -> str | None:
	spec = SUPPRESSIBLE.get(kind)
	if not spec:
		return None
	return kind + "|" + "|".join(str(row.get(field) or "") for field in spec[0])


def active() -> dict:
	"""{alert key: suppression} for suppressions in force today."""
	result = {}
	for s in frappe.get_all(
		"Alert Suppression",
		filters={"status": ACTIVE},
		fields=["name", "alert_key", "reason", "valid_to", "suppressed_by", "suppressed_on"],
		limit_page_length=0,
	):
		if s.valid_to and getdate(s.valid_to) < getdate(today()):
			continue
		result[s.alert_key] = s
	return result


def split(kind: str, rows: list, suppressions: dict | None = None) -> tuple[list, list]:
	"""Rows of a control list → (open alerts, suppressed alerts). Every row gets its alert_key."""
	if kind not in SUPPRESSIBLE:
		return rows, []
	suppressions = active() if suppressions is None else suppressions
	open_rows, hidden = [], []
	for row in rows:
		key = alert_key(kind, row)
		row["alert_key"] = key
		s = suppressions.get(key)
		if s:
			row["suppression"] = s.name
			row["suppression_reason"] = s.reason
			row["suppressed_by"] = s.suppressed_by
			row["suppressed_until"] = str(s.valid_to) if s.valid_to else None
			hidden.append(row)
		else:
			open_rows.append(row)
	return open_rows, hidden


def suppress(kind: str, title: str, rows: list, keys: list, reason: str, valid_to=None) -> list[str]:
	"""Suppresses the alerts of `rows` whose keys are in `keys`. Returns the journal entries."""
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Напишите, почему замечание гасится: это попадёт в журнал"))
	if kind not in SUPPRESSIBLE:
		frappe.throw(
			_(
				"Замечания этого списка не гасятся: для лишних доступов есть исключения, для кадровых событий — отметка «Обработано»"
			)
		)
	if valid_to and getdate(valid_to) < getdate(today()):
		frappe.throw(_("Дата «погашено до» уже прошла"))
	wanted = set(keys or [])
	if not wanted:
		frappe.throw(_("Не выбрано ни одного замечания"))
	current = active()
	by_key = {alert_key(kind, r): r for r in rows}
	unknown = wanted - set(by_key)
	if unknown:
		frappe.throw(_("Замечания уже нет в списке — обновите страницу"))
	subject_of = SUPPRESSIBLE[kind][1]
	created = []
	for key in sorted(wanted):
		if key in current:
			continue  # already suppressed: the journal keeps the first decision
		row = by_key[key]
		doc = frappe.get_doc(
			{
				"doctype": "Alert Suppression",
				"status": ACTIVE,
				"alert_kind": kind,
				"alert_title": title,
				"alert_key": key,
				"subject": subject_of(row)[:140],
				"person": row.get("person") or None,
				"ref_doctype": row.get("ref_doctype") or None,
				"ref_name": row.get("ref") or None,
				"reason": reason,
				"valid_to": getdate(valid_to) if valid_to else None,
				"suppressed_by": frappe.session.user,
				"suppressed_on": now_datetime(),
			}
		)
		doc.flags.from_registry = True
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	return created


def restore(name: str, reason: str) -> str:
	"""The alert shows again; the entry stays in the journal with who and why returned it."""
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Напишите, почему замечание возвращается"))
	doc = frappe.get_doc("Alert Suppression", name)
	if doc.status != ACTIVE:
		frappe.throw(_("Замечание уже не погашено"))
	doc.status = RESTORED
	doc.restore_reason = reason
	doc.restored_by = frappe.session.user
	doc.restored_on = now_datetime()
	doc.flags.from_registry = True
	doc.save(ignore_permissions=True, ignore_version=False)
	return doc.name


def expire():
	"""Nightly: suppressions past their date become «Истёк срок» — the alert is back in the lists."""
	for name in frappe.get_all(
		"Alert Suppression", filters={"status": ACTIVE, "valid_to": ["<", today()]}, pluck="name"
	):
		doc = frappe.get_doc("Alert Suppression", name)
		doc.status = EXPIRED
		doc.flags.from_registry = True
		doc.save(ignore_permissions=True, ignore_version=False)


def journal(limit: int = 2000) -> list:
	rows = frappe.get_all(
		"Alert Suppression",
		fields=[
			"name",
			"status",
			"alert_kind",
			"alert_title",
			"subject",
			"person",
			"ref_doctype",
			"ref_name",
			"reason",
			"valid_to",
			"suppressed_by",
			"suppressed_on",
			"restore_reason",
			"restored_by",
			"restored_on",
		],
		order_by="suppressed_on desc",
		limit_page_length=limit,
	)
	names = dict(
		frappe.get_all(
			"User",
			filters={
				"name": [
					"in",
					list({r.suppressed_by for r in rows} | {r.restored_by for r in rows if r.restored_by}),
				]
			},
			fields=["name", "full_name"],
			as_list=True,
		)
	)
	for r in rows:
		r.suppressed_by_name = names.get(r.suppressed_by) or r.suppressed_by
		r.restored_by_name = names.get(r.restored_by) or r.restored_by
		for field in ("valid_to", "suppressed_on", "restored_on"):
			r[field] = str(r[field]) if r[field] else None
	return rows


# --------------------------------------------------------------------------- desk reports

SUPPRESSED_COLUMNS = [
	{"fieldname": "suppression_reason", "label": _("Почему погашено"), "fieldtype": "Data", "width": 320},
	{
		"fieldname": "suppressed_by",
		"label": _("Погасил"),
		"fieldtype": "Link",
		"options": "User",
		"width": 160,
	},
	{"fieldname": "suppressed_until", "label": _("Погашено до"), "fieldtype": "Date", "width": 110},
	{
		"fieldname": "suppression",
		"label": _("Запись журнала"),
		"fieldtype": "Link",
		"options": "Alert Suppression",
		"width": 130,
	},
]


def by_ref(kind: str, doctype: str, field: str = "name"):
	"""Key of an alert about one record (a 1C user, an AD account, a Bitrix24 user)."""
	return lambda row: f"{kind}|{doctype}|{row.get(field) or ''}"


def hide_in_report(kind: str, result, filters=None, key=None):
	"""A desk report without the alerts suppressed in the registry app.

	With the filter «Показать погашенные» the report shows only them, with the reason and who
	suppressed them. Returns (columns, data, message) for a Script Report.
	"""
	columns, data = result[0], result[1]
	filters = frappe._dict(filters or {})
	key = key or (lambda row: alert_key(kind, row))
	current = active()
	shown, hidden = [], []
	for row in data:
		s = current.get(key(row))
		if not s:
			shown.append(row)
			continue
		row["suppression"] = s.name
		row["suppression_reason"] = s.reason
		row["suppressed_by"] = s.suppressed_by
		row["suppressed_until"] = s.valid_to
		hidden.append(row)
	if filters.get("show_suppressed"):
		return list(columns) + SUPPRESSED_COLUMNS, hidden, None
	message = (
		_(
			"Скрыто погашенных замечаний: {0}. Показать их — галочка «Показать погашенные»; все гашения — «Журнал гашений»."
		).format(len(hidden))
		if hidden
		else None
	)
	return columns, shown, message
