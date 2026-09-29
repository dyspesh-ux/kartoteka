"""The morning digest: what appeared in the control lists since the previous digest, by email.

Every digest is kept in «Registry Digest Log» together with the keys of the open alerts; the next
digest compares the current alerts with them, so «new» means new since the last digest (on Monday —
since Friday). Suppressed alerts are not counted. Besides alerts: sources that did not load, changes
of shared folder permissions, suppressions that end soon, overdue access reviews.
"""

import json
from datetime import timedelta
from html import escape

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, get_url, getdate, now_datetime, today

from access_registry import app_access
from access_registry.access_roles import suppression

# control lists in the digest, in the order of the letter
KINDS = [
	"dismissed",
	"events",
	"sod",
	"excess",
	"privileged",
	"unlinked",
	"missing",
	"shares",
	"processes",
	"quality",
]
# alerts of lists that are not suppressed: how to tell one from another and how to name it
OTHER_KEYS = {
	"excess": (("person", "entitlement", "title"), lambda r: f"{r.get('full_name')}: {r.get('title')}"),
	"missing": (("person", "entitlement", "title"), lambda r: f"{r.get('full_name')}: {r.get('title')}"),
	"events": (
		("name",),
		lambda r: (
			f"{r.get('full_name')}: {r.get('event_type')} {getdate(r.get('event_date')).strftime('%d.%m.%Y') if r.get('event_date') else ''}".strip()
		),
	),
}
LIST_LIMIT = 15  # new alerts named in the letter per list; the rest are counted
STALE_HOURS = 26  # a source without a successful load for longer is a problem
EXPIRING_DAYS = 7
SENT = "Отправлено"
NOTHING = "Нового нет"
PREVIEW = "Предпросмотр"
FAILED = "Ошибка"


def key_of(kind, row) -> str:
	if kind in suppression.SUPPRESSIBLE:
		return suppression.alert_key(kind, row)
	fields = OTHER_KEYS[kind][0]
	return kind + "|" + "|".join(str(row.get(f) or "") for f in fields)


def subject_of(kind, row) -> str:
	spec = suppression.SUPPRESSIBLE.get(kind) or OTHER_KEYS[kind]
	return spec[1](row)


# --------------------------------------------------------------------------- collecting


def collect() -> dict:
	"""{kind: {key: subject}} of the open (not suppressed) alerts of every list of the digest."""
	from access_registry.registry import api

	suppressed = suppression.active()
	result = {}
	for kind in KINDS:
		_columns, rows = api._control_rows(kind)
		open_rows, _hidden = suppression.split(kind, rows, suppressed)
		result[kind] = {key_of(kind, r): subject_of(kind, r) for r in open_rows}
	return result


def previous_log():
	logs = frappe.get_all(
		"Registry Digest Log",
		filters={"status": ["in", [SENT, NOTHING]]},
		fields=["name", "sent_on", "alert_keys"],
		order_by="sent_on desc",
		limit=1,
	)
	return logs[0] if logs else None


def source_problems() -> list[str]:
	from access_registry.registry.api import sources

	problems = []
	stale_before = now_datetime() - timedelta(hours=STALE_HOURS)
	for s in sources():
		if not s["enabled"]:
			continue
		title = f"{s['kind']} {s['name']}"
		if s["state"] in ("error", "warn"):
			problems.append(f"{title}: {s['status']}")
		elif not s["last"]:
			problems.append(_("{0}: ещё не загружался").format(title))
		elif get_datetime(s["last"]) < stale_before:
			problems.append(_("{0}: последняя успешная загрузка {1}").format(title, s["last"][:16]))
	return problems


def folder_changes(since) -> list[str]:
	"""Shared folders whose permissions changed or which got their own permissions since `since`."""
	if not frappe.db.table_exists("Folder ACL"):
		return []
	names = set(
		frappe.get_all(
			"Version", filters={"ref_doctype": "Folder ACL", "creation": [">", since]}, pluck="docname"
		)
	)
	names |= set(frappe.get_all("Folder ACL", filters={"creation": [">", since]}, pluck="name"))
	if not names:
		return []
	shares = dict(
		frappe.get_all("File Share", fields=["name", "share_name"], as_list=True, limit_page_length=0)
	)
	rows = frappe.get_all(
		"Folder ACL",
		filters={"name": ["in", list(names)]},
		fields=["name", "share", "path", "missing_in_source", "creation"],
		order_by="share, path",
	)
	result = []
	for r in rows:
		label = f"{shares.get(r.share) or r.share} {r.path}"
		if r.missing_in_source:
			result.append(_("{0}: особые права сняты").format(label))
		elif get_datetime(r.creation) > get_datetime(since):
			result.append(_("{0}: появились особые права").format(label))
		else:
			result.append(_("{0}: права изменились").format(label))
	return result


def expiring_suppressions() -> list[str]:
	return [
		_("{0} — до {1}").format(s.subject, getdate(s.valid_to).strftime("%d.%m.%Y"))
		for s in frappe.get_all(
			"Alert Suppression",
			filters={
				"status": suppression.ACTIVE,
				"valid_to": ["between", [today(), add_days(today(), EXPIRING_DAYS)]],
			},
			fields=["subject", "valid_to"],
			order_by="valid_to",
		)
	]


def overdue_reviews() -> list[str]:
	return [
		_("«{0}»: срок {1}, не решено {2} из {3}").format(
			r.title,
			getdate(r.due_date).strftime("%d.%m.%Y"),
			(r.items_total or 0) - (r.items_done or 0),
			r.items_total or 0,
		)
		for r in frappe.get_all(
			"Access Review",
			filters={"status": "Идёт", "due_date": ["<", today()]},
			fields=["title", "due_date", "items_total", "items_done"],
		)
	]


def build() -> dict:
	"""Everything of one digest: sections, counters, the keys to compare the next digest with."""
	from access_registry.registry.api import CONTROLS

	current = collect()
	prev = previous_log()
	prev_keys = json.loads(prev.alert_keys) if prev and prev.alert_keys else None
	since = prev.sent_on if prev else add_days(now_datetime(), -1)
	lists, new_total, resolved_total = [], 0, 0
	for kind in KINDS:
		now_keys = current[kind]
		before = set(prev_keys.get(kind, [])) if prev_keys is not None else None
		new = [k for k in now_keys if before is not None and k not in before]
		resolved = len(before - set(now_keys)) if before is not None else 0
		new_total += len(new)
		resolved_total += resolved
		lists.append(
			{
				"kind": kind,
				"title": CONTROLS[kind],
				"open": len(now_keys),
				"delta": len(now_keys) - len(before) if before is not None else None,
				"new": sorted(now_keys[k] for k in new),
				"resolved": resolved,
				"url": get_url(f"/registry#/control/{kind}"),
			}
		)
	return {
		"first": prev_keys is None,
		"since": str(since),
		"lists": lists,
		"new_total": new_total,
		"resolved_total": resolved_total,
		"open_total": sum(len(v) for v in current.values()),
		"sources": source_problems(),
		"folders": folder_changes(since),
		"expiring": expiring_suppressions(),
		"reviews": overdue_reviews(),
		"keys": {kind: sorted(keys) for kind, keys in current.items()},
	}


def has_news(d) -> bool:
	return bool(d["new_total"] or d["sources"] or d["folders"] or d["expiring"] or d["reviews"])


# --------------------------------------------------------------------------- the letter

GREEN = "#2e9e6a"
INK = "#1e2421"
MUTED = "#7b837f"
LINE = "#e5e8e6"
SOFT = "#f6f7f6"


def subject_line(d) -> str:
	day = getdate(today()).strftime("%d.%m.%Y")
	if d["first"]:
		return _("Реестр доступа: сводка на {0} — открытых замечаний {1}").format(day, d["open_total"])
	if d["new_total"]:
		return _("Реестр доступа: сводка на {0} — новых замечаний {1}").format(day, d["new_total"])
	return _("Реестр доступа: сводка на {0} — нового нет").format(day)


def _plural_new(n) -> str:
	"""«+1 новое», «+2 новых», «+21 новое»."""
	return _("новое") if n % 10 == 1 and n % 100 != 11 else _("новых")


def _section(title, items, empty=None):
	if not items and not empty:
		return ""
	body = (
		"".join(f'<li style="margin:2px 0">{escape(str(i))}</li>' for i in items)
		if items
		else f'<li style="color:{MUTED}">{escape(empty)}</li>'
	)
	return (
		f'<h3 style="font-size:15px;margin:24px 0 8px;color:{INK}">{escape(title)}</h3>'
		f'<ul style="margin:0;padding-left:18px;color:{INK}">{body}</ul>'
	)


def render(d) -> str:
	rows = []
	for item in d["lists"]:
		delta = item["delta"]
		delta_text = "" if delta in (None, 0) else (f" (+{delta})" if delta > 0 else f" ({delta})")
		weight = "700" if item["new"] else "400"
		rows.append(
			f'<tr><td style="padding:6px 12px;border-bottom:1px solid {LINE}">'
			f'<a href="{item["url"]}" style="color:{INK};text-decoration:none">{escape(item["title"])}</a></td>'
			f'<td style="padding:6px 12px;border-bottom:1px solid {LINE};text-align:right;font-weight:{weight}">'
			f'{item["open"]}<span style="color:{MUTED};font-weight:400">{delta_text}</span></td>'
			f'<td style="padding:6px 12px;border-bottom:1px solid {LINE};text-align:right;color:{GREEN}">'
			f"{'+' + str(len(item['new'])) + ' ' + _plural_new(len(item['new'])) if item['new'] else ''}</td></tr>"
		)
	news = []
	for item in d["lists"]:
		if not item["new"]:
			continue
		shown = item["new"][:LIST_LIMIT]
		more = len(item["new"]) - len(shown)
		lines = "".join(f'<li style="margin:2px 0">{escape(s)}</li>' for s in shown)
		if more:
			lines += f'<li style="color:{MUTED}">{_("и ещё {0}").format(more)}</li>'
		news.append(
			f'<h3 style="font-size:15px;margin:24px 0 8px"><a href="{item["url"]}" style="color:{GREEN};'
			f'text-decoration:none">{escape(item["title"])}</a> · {len(item["new"])}</h3>'
			f'<ul style="margin:0;padding-left:18px;color:{INK}">{lines}</ul>'
		)
	if d["first"]:
		intro = _(
			"Первая сводка: ниже — сколько замечаний открыто сейчас. Со следующей сводки — что появилось нового."
		)
	elif d["new_total"] or d["resolved_total"]:
		intro = _("С прошлой сводки: новых замечаний {0}, закрылось {1}.").format(
			d["new_total"], d["resolved_total"]
		)
	else:
		intro = _("С прошлой сводки новых замечаний нет.")
	return f"""<div style="font-family:'Century Gothic','Segoe UI',Arial,sans-serif;font-size:14px;line-height:20px;color:{INK};max-width:720px">
	<p style="margin:0 0 4px;font-size:20px;font-weight:700">{escape(_("Реестр доступа: утренняя сводка"))}</p>
	<p style="margin:0 0 16px;color:{MUTED}">{escape(intro)} {escape(_("Погашенные замечания не учитываются."))}</p>
	<table style="border-collapse:collapse;width:100%;background:{SOFT};border:1px solid {LINE};border-radius:8px">
		<tr><th style="text-align:left;padding:8px 12px;color:{MUTED};font-weight:400">{escape(_("Список"))}</th>
		<th style="text-align:right;padding:8px 12px;color:{MUTED};font-weight:400">{escape(_("Открыто (изменение)"))}</th><th></th></tr>
		{"".join(rows)}
	</table>
	{"".join(news)}
	{_section(_("Загрузки: что не так"), d["sources"])}
	{_section(_("Права на общих папках изменились"), d["folders"][:30] + ([_("и ещё {0}").format(len(d["folders"]) - 30)] if len(d["folders"]) > 30 else []))}
	{_section(_("Гашения, у которых скоро кончается срок"), d["expiring"])}
	{_section(_("Пересмотр доступа просрочен"), d["reviews"])}
	<p style="margin:28px 0 0"><a href="{get_url("/registry#/control")}" style="background:{GREEN};color:#fff;text-decoration:none;padding:10px 20px;border-radius:44px;font-weight:700">{escape(_("Открыть «Контроль»"))}</a></p>
	<p style="margin:24px 0 0;color:{MUTED};font-size:12px">{escape(_("Сводку настраивает администратор реестра: «Утренняя сводка» в разделе «Access Registry»."))}</p>
</div>"""


# --------------------------------------------------------------------------- sending


def recipients(settings) -> list[str]:
	"""Emails of the chosen users who may read the registry (enabled users with a registry role)."""
	emails = []
	for row in settings.recipients or []:
		user = frappe.db.get_value("User", row.user, ["enabled", "email"], as_dict=True)
		if not user or not user.enabled or not user.email:
			continue
		# the letter shows every control list: only for those who see «Обзор» and all of «Контроль»
		a = app_access.access(row.user)
		if a["sections"]["overview"] and a["sections"]["control"] and a["all_lists"]:
			emails.append(user.email)
	return sorted(set(emails))


def run(preview: bool = False, force: bool = False):
	"""Builds the digest, sends it (unless preview) and records it in the log. Returns the log."""
	settings = frappe.get_single("Registry Digest")
	log = frappe.new_doc("Registry Digest Log")
	log.sent_on = now_datetime()
	try:
		d = build()
		log.subject = subject_line(d)[:140]
		log.message_html = render(d)
		log.new_alerts, log.resolved_alerts, log.open_alerts = (
			d["new_total"],
			d["resolved_total"],
			d["open_total"],
		)
		log.alert_keys = json.dumps(d["keys"], ensure_ascii=False)
		to = recipients(settings)
		log.recipients = "\n".join(to)
		if preview:
			log.status = PREVIEW
			log.alert_keys = None  # a preview is not a baseline for the next digest
		elif settings.skip_if_nothing_new and not d["first"] and not has_news(d) and not force:
			log.status = NOTHING
		elif not to:
			log.status = FAILED
			log.error = _("Нет получателей с доступом к реестру")
		else:
			frappe.sendmail(recipients=to, subject=log.subject, message=log.message_html)
			log.status = SENT
	except frappe.OutgoingEmailError:
		log.status = FAILED
		log.error = _(
			"Не настроена исходящая почта: Email Account с «Использовать для исходящих» и «По умолчанию»"
		)
	except Exception:
		log.status = FAILED
		log.error = frappe.get_traceback()[-2000:]
	log.insert(ignore_permissions=True)
	if not preview:
		frappe.db.set_single_value(
			"Registry Digest",
			{"last_sent": log.sent_on, "last_status": f"{log.status} ({log.name})"},
		)
	return log


def scheduled():
	"""Hourly at :05: sends the digest in the chosen hour, once a day, on working days if so set."""
	settings = frappe.get_single("Registry Digest")
	if not settings.enabled:
		return
	now = now_datetime()
	if now.hour != int(settings.send_hour or 0):
		return
	if settings.weekdays_only and now.weekday() >= 5:
		return
	if (
		settings.last_sent
		and getdate(settings.last_sent) == getdate(now)
		and (settings.last_status or "").startswith((SENT, NOTHING))
	):
		return
	run()
