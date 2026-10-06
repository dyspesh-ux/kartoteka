"""Notification rules: who gets what («Registry Notification Rule»).

A rule names an event, the recipients (users and members of access profiles), the channels (email,
the bell of the app) and when to send (within 15 minutes or once a day). Each run collects the
current items of the event, compares them with the ones the rule already knew (``state``) and sends
the new ones. The first run only remembers what there is: nobody gets a flood of old alerts.

Every recipient gets only what the registry lets them see: control lists and systems of their access,
sources of their systems, people only with «Сотрудники», the helpdesk only with «Техподдержка», AD
plans only with the right to see them (decisions only for ИБ, never on their own plan).
"""

import json
from collections import defaultdict
from html import escape

import frappe
from frappe import _
from frappe.utils import get_url, getdate, now_datetime

from access_registry import app_access as aa

CONTROL = "Новые замечания в «Контроле»"
SOURCES = "Загрузка источника не удалась"
HR = "Кадровые события"
AD_PLAN = "План изменений AD"
HELPDESK = "Техподдержка: просроченные заявки"
DAILY = "Раз в день"
LIMIT = 30  # items named in one message; the rest are counted
KEEP_NOTICES_DAYS = 90


class Item(frappe._dict):
	"""key — what makes it new; text, url — what the recipient sees; group — heading in the message;
	visible(user, access) — whether this recipient may see it."""


# --------------------------------------------------------------------------- recipients


def recipients(rule) -> list[str]:
	users = {r.user for r in rule.users or [] if r.user}
	profiles = [r.profile for r in rule.profiles or [] if r.profile]
	if profiles:
		users |= set(
			frappe.get_all(
				"Registry Access Member",
				filters={"parenttype": "Registry Access Profile", "parent": ["in", profiles]},
				pluck="user",
			)
		)
	enabled = set(
		frappe.get_all("User", filters={"name": ["in", list(users) or [""]], "enabled": 1}, pluck="name")
	)
	return sorted(u for u in enabled if u not in ("Guest",))


# --------------------------------------------------------------------------- collectors


def _control_items(rule) -> list[Item]:
	from access_registry.access_roles import digest, suppression
	from access_registry.registry.api import CONTROLS, _control_rows_all

	chosen = [
		CONTROL_BY_TITLE[r.control_list]
		for r in rule.control_lists or []
		if r.control_list in CONTROL_BY_TITLE
	]
	kinds = [k for k in digest.KINDS if not chosen or k in chosen]
	suppressed = suppression.active()
	items = []
	for kind in kinds:
		_columns, rows = _control_rows_all(kind)
		open_rows, _hidden = suppression.split(kind, rows, suppressed)
		for row in open_rows:
			system = row.get("system")
			items.append(
				Item(
					key=digest.key_of(kind, row),
					text=digest.subject_of(kind, row),
					group=CONTROLS[kind],
					url=get_url(f"/registry#/control/{kind}"),
					visible=lambda user, a, kind=kind, system=system: (
						kind in a["lists"] and aa.system_allowed(system, a["systems"])
					),
				)
			)
	return items


def _source_items(rule) -> list[Item]:
	from access_registry.registry.api import _source_allowed, sources

	items = []
	for s in sources():
		if s["enabled"] and s["state"] in ("error", "warn"):
			items.append(
				Item(
					key=f"{s['kind']}|{s['name']}|{s['status']}",
					text=f"{s['kind']} {s['title'] or s['name']}: {s['status']}",
					group=_("Загрузки"),
					url=get_url("/registry#/sources"),
					visible=lambda user, a, kind=s["kind"]: (
						(a["sections"]["sources"] or a["sections"]["overview"])
						and _source_allowed(kind, a["systems"])
					),
				)
			)
	return items


def _hr_items(rule) -> list[Item]:
	# an hour of overlap: events created while the previous run was working are not lost; the ones
	# already sent are in the rule's state
	since = frappe.utils.add_to_date(rule.last_run, hours=-1) if rule.last_run else None
	rows = frappe.get_all(
		"HR Event",
		filters={"creation": [">", since]} if since else {},
		fields=["name", "event_type", "event_date", "person"],
		order_by="event_date",
		limit=500,
	)
	names = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", list({r.person for r in rows if r.person}) or [""]]},
			fields=["name", "full_name"],
			as_list=True,
		)
	)
	return [
		Item(
			key=r.name,
			text=f"{r.event_type}: {names.get(r.person) or r.person} {getdate(r.event_date).strftime('%d.%m.%Y') if r.event_date else ''}".strip(),
			group=r.event_type,
			url=get_url(f"/registry#/person/{r.person}")
			if r.person
			else get_url("/registry#/control/events"),
			visible=lambda user, a: bool(a["sections"]["people"]),
		)
		for r in rows
	]


def _ad_plan_items(rule) -> list[Item]:
	from access_registry.registry.api import _ad_plan_rights

	items = []
	for p in frappe.get_all(
		"AD Change Plan",
		filters={"creation": [">", frappe.utils.add_days(now_datetime(), -30)]},
		fields=["name", "status", "owner", "domain", "disable_count", "update_count", "decision_comment"],
	):
		url = get_url(f"/registry#/ad-plan/{p.name}")
		what = _("отключить {0}, изменить {1}").format(p.disable_count, p.update_count)
		if p.status == "Черновик":
			items.append(
				Item(
					key=f"{p.name}|draft",
					text=_("{0} ({1}): {2} — нужно решение ИБ").format(p.name, p.domain, what),
					group=_("Ждут одобрения"),
					url=url,
					visible=lambda user, a, owner=p.owner: bool(a.get("ad_approver")) and user != owner,
				)
			)
		else:
			items.append(
				Item(
					key=f"{p.name}|{p.status}",
					text=_("{0} ({1}): {2}{3}").format(
						p.name,
						p.status.lower(),
						what,
						f" — {p.decision_comment}" if p.decision_comment else "",
					),
					group=_("Решение принято"),
					url=url,
					visible=lambda user, a: _ad_plan_rights(a)["see"],
				)
			)
	return items


def _helpdesk_items(rule) -> list[Item]:
	from frappe.utils import today

	rows = frappe.get_all(
		"B24 Smart Item",
		filters={"state": "Открыта", "deadline": ["<", today()]},
		fields=["name", "item_id", "title", "deadline", "assigned_name", "url"],
		order_by="deadline",
		limit=1000,
	)
	return [
		Item(
			key=r.name,
			text=_("№{0} {1} — срок {2}, ответственный: {3}").format(
				r.item_id,
				r.title,
				getdate(r.deadline).strftime("%d.%m.%Y"),
				r.assigned_name or _("не назначен"),
			),
			group=_("Просрочены"),
			url=r.url or get_url("/registry#/support"),
			visible=lambda user, a: bool(a["sections"].get("support")),
		)
		for r in rows
	]


COLLECTORS = {
	CONTROL: _control_items,
	SOURCES: _source_items,
	HR: _hr_items,
	AD_PLAN: _ad_plan_items,
	HELPDESK: _helpdesk_items,
}
CONTROL_BY_TITLE = aa.CONTROL_BY_TITLE


# --------------------------------------------------------------------------- running


def due(rule, now=None) -> bool:
	now = now or now_datetime()
	if not rule.enabled:
		return False
	if rule.weekdays_only and now.weekday() >= 5:
		return False
	if rule.frequency == DAILY:
		if now.hour != int(rule.send_hour or 0):
			return False
		return not (rule.last_run and getdate(rule.last_run) == getdate(now))
	return True


def scheduled(commit: bool = True):
	"""Every 15 minutes: the rules that are due."""
	for name in frappe.get_all("Registry Notification Rule", filters={"enabled": 1}, pluck="name"):
		rule = frappe.get_doc("Registry Notification Rule", name)
		if due(rule):
			try:
				run(rule)
			except Exception:
				if commit:
					frappe.db.rollback()
				frappe.log_error(title=f"Уведомления: правило {rule.title}")
				frappe.db.set_value(
					"Registry Notification Rule",
					name,
					"last_status",
					_("Ошибка: см. Error Log"),
					update_modified=False,
				)
			if commit:
				frappe.db.commit()
	frappe.db.delete(
		"Registry Notice", {"creation": ["<", frappe.utils.add_days(now_datetime(), -KEEP_NOTICES_DAYS)]}
	)


def run(rule, send: bool = True) -> dict:
	"""Collects, finds what is new for the rule and sends it. Returns {user: [items]} (what was or
	would be sent). The first run of a rule only remembers the current items."""
	items = COLLECTORS[rule.event](rule)
	known = set(json.loads(rule.state or "[]")) if rule.state else None
	new = [i for i in items if known is not None and i.key not in known]
	per_user = {}
	for user in recipients(rule):
		a = aa.access(user)
		a["lists"], a["systems"] = aa.control_lists(user), aa.systems_of(a)
		visible = [i for i in new if i.visible(user, a)]
		if visible:
			per_user[user] = visible
	if send:
		mail_failed = False
		for user, user_items in per_user.items():
			mail_failed = not deliver(rule, user, user_items) or mail_failed
		rule.db_set(
			{
				"state": json.dumps(sorted({i.key for i in items}), ensure_ascii=False),
				"last_run": now_datetime(),
				"last_status": _("первая проверка: запомнено {0}").format(len(items))
				if known is None
				else _("новых {0}, отправлено {1} получателям").format(len(new), len(per_user))
				+ (_("; почта не отправлена — не настроен исходящий ящик") if mail_failed else "")
				if new
				else _("нового нет"),
			},
			update_modified=False,
		)
	return per_user


def deliver(rule, user, items) -> bool:
	"""Sends to one recipient; False when the email could not be queued (no outgoing account)."""
	title = _("{0}: {1}").format(
		rule.title, _("новых {0}").format(len(items)) if len(items) > 1 else items[0].text
	)
	if rule.channel_app:
		frappe.get_doc(
			{
				"doctype": "Registry Notice",
				"for_user": user,
				"title": title[:140],
				"body": "\n".join(i.text for i in items[:LIMIT])
				+ (f"\n{_('и ещё {0}').format(len(items) - LIMIT)}" if len(items) > LIMIT else ""),
				"link": items[0].url if len(items) == 1 else _app_link(rule),
				"rule": rule.name,
			}
		).insert(ignore_permissions=True)
	if rule.channel_email:
		email = frappe.db.get_value("User", user, "email")
		if email:
			try:
				frappe.sendmail(
					recipients=[email], subject=title[:140], message=render(rule, items), now=False
				)
			except frappe.OutgoingEmailError:
				return False
	return True


def _app_link(rule) -> str:
	return get_url(
		{
			CONTROL: "/registry#/control",
			SOURCES: "/registry#/sources",
			HR: "/registry#/control/events",
			AD_PLAN: "/registry#/ad-plans",
			HELPDESK: "/registry#/support",
		}[rule.event]
	)


def render(rule, items) -> str:
	from access_registry.access_roles.digest import GREEN, INK, MUTED

	groups = defaultdict(list)
	for i in items:
		groups[i.group].append(i)
	blocks = []
	for group, group_items in groups.items():
		shown = group_items[:LIMIT]
		lines = "".join(
			f'<li style="margin:2px 0"><a href="{escape(i.url)}" style="color:{INK}">{escape(i.text)}</a></li>'
			for i in shown
		)
		if len(group_items) > LIMIT:
			lines += (
				f'<li style="color:{MUTED}">{escape(_("и ещё {0}").format(len(group_items) - LIMIT))}</li>'
			)
		blocks.append(
			f'<h3 style="font-size:15px;margin:20px 0 8px">{escape(group)} · {len(group_items)}</h3>'
			f'<ul style="margin:0;padding-left:18px">{lines}</ul>'
		)
	return f"""<div style="font-family:'Century Gothic','Segoe UI',Arial,sans-serif;font-size:14px;line-height:20px;color:{INK};max-width:720px">
	<p style="margin:0 0 4px;font-size:18px;font-weight:700">{escape(rule.title)}</p>
	<p style="margin:0 0 8px;color:{MUTED}">{escape(rule.event)}</p>
	{"".join(blocks)}
	<p style="margin:24px 0 0"><a href="{escape(_app_link(rule))}" style="background:{GREEN};color:#fff;text-decoration:none;padding:10px 20px;border-radius:44px;font-weight:700">{escape(_("Открыть реестр"))}</a></p>
	<p style="margin:20px 0 0;color:{MUTED};font-size:12px">{escape(_("Уведомления настраивает администратор реестра: «Уведомления» в приложении."))}</p>
</div>"""
