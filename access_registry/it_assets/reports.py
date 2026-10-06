"""Reports on equipment from Snipe-IT: who has what, and the movements (checkouts, checkins, audits)."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today


def assets(filters=None):
	"""Every asset with the employee it is handed out to."""
	filters = frappe._dict(filters or {})
	conditions = ["a.missing_in_source = 0"]
	params = {}
	if not filters.include_archived:
		conditions.append("ifnull(a.status_type, '') != 'archived'")
	for key in ("server", "category", "status_label", "person"):
		if filters.get(key):
			conditions.append(f"a.{key} = %({key})s")
			params[key] = filters.get(key)
	if filters.only_handed_out:
		conditions.append("a.assigned_type = 'user'")
	if filters.only_not_working:
		conditions.append("ifnull(a.person, '') != '' and p.status != 'Работает'")
	rows = frappe.db.sql(
		f"""select a.name as asset, a.asset_name, a.asset_tag, a.serial, a.category, a.model, a.manufacturer,
			a.status_label, a.location, a.company, a.assigned_type, a.assigned_name, a.person,
			p.full_name as employee, p.status as person_status, a.last_checkout, a.expected_checkin,
			a.last_audit_date, a.next_audit_date, a.purchase_date, a.purchase_cost, a.warranty_expires
		from `tabIT Asset` a left join `tabPerson` p on p.name = a.person
		where {" and ".join(conditions)}
		order by a.category, a.asset_name""",
		params,
		as_dict=True,
	)
	kinds = {"user": _("сотруднику"), "location": _("в место"), "asset": _("к технике")}
	for r in rows:
		r.assigned_type = kinds.get(r.assigned_type, _("не выдана"))
	return [
		{"fieldname": "asset_name", "label": _("Техника"), "fieldtype": "Data", "width": 200},
		{"fieldname": "asset_tag", "label": _("Инв. номер"), "fieldtype": "Data", "width": 110},
		{"fieldname": "category", "label": _("Категория"), "fieldtype": "Data", "width": 120},
		{"fieldname": "model", "label": _("Модель"), "fieldtype": "Data", "width": 150},
		{"fieldname": "status_label", "label": _("Статус"), "fieldtype": "Data", "width": 110},
		{"fieldname": "employee", "label": _("Сотрудник"), "fieldtype": "Data", "width": 200},
		{"fieldname": "person_status", "label": _("Статус сотрудника"), "fieldtype": "Data", "width": 110},
		{"fieldname": "assigned_type", "label": _("Выдана"), "fieldtype": "Data", "width": 100},
		{"fieldname": "assigned_name", "label": _("Кому в Snipe-IT"), "fieldtype": "Data", "width": 170},
		{"fieldname": "location", "label": _("Место"), "fieldtype": "Data", "width": 120},
		{"fieldname": "last_checkout", "label": _("Выдана когда"), "fieldtype": "Datetime", "width": 140},
		{"fieldname": "expected_checkin", "label": _("Вернуть до"), "fieldtype": "Date", "width": 100},
		{"fieldname": "next_audit_date", "label": _("Следующий аудит"), "fieldtype": "Date", "width": 110},
		{"fieldname": "serial", "label": _("Серийный номер"), "fieldtype": "Data", "width": 130},
		{"fieldname": "purchase_date", "label": _("Куплена"), "fieldtype": "Date", "width": 100},
		{"fieldname": "purchase_cost", "label": _("Стоимость"), "fieldtype": "Float", "width": 100},
		{"fieldname": "warranty_expires", "label": _("Гарантия до"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "asset",
			"label": _("Карточка"),
			"fieldtype": "Link",
			"options": "IT Asset",
			"width": 120,
		},
		{
			"fieldname": "person",
			"label": _("Карточка сотрудника"),
			"fieldtype": "Link",
			"options": "Person",
			"width": 140,
		},
	], rows


def movements(filters=None):
	"""Checkouts, checkins, audits and changes of equipment for a period."""
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.from_date or add_days(today(), -30))
	to_date = getdate(filters.to_date or today())
	conditions = ["date(e.event_date) between %(from_date)s and %(to_date)s"]
	params = {"from_date": from_date, "to_date": to_date}
	if filters.action:
		conditions.append("e.action = %(action)s")
		params["action"] = filters.action
	if filters.person:
		conditions.append("e.person = %(person)s")
		params["person"] = filters.person
	if filters.only_handovers:
		conditions.append("e.action_type in ('checkout', 'checkin from')")
	rows = frappe.db.sql(
		f"""select e.event_date, e.action, e.item_name, e.asset, a.asset_tag, a.category, e.target_name,
			e.person, p.full_name as employee, p.status as person_status, e.admin_name, e.note
		from `tabIT Asset Event` e
			left join `tabIT Asset` a on a.name = e.asset
			left join `tabPerson` p on p.name = e.person
		where {" and ".join(conditions)}
		order by e.event_date desc""",
		params,
		as_dict=True,
	)
	return [
		{"fieldname": "event_date", "label": _("Когда"), "fieldtype": "Datetime", "width": 140},
		{"fieldname": "action", "label": _("Действие"), "fieldtype": "Data", "width": 110},
		{"fieldname": "item_name", "label": _("Техника"), "fieldtype": "Data", "width": 190},
		{"fieldname": "asset_tag", "label": _("Инв. номер"), "fieldtype": "Data", "width": 110},
		{"fieldname": "category", "label": _("Категория"), "fieldtype": "Data", "width": 120},
		{"fieldname": "employee", "label": _("Сотрудник"), "fieldtype": "Data", "width": 190},
		{"fieldname": "target_name", "label": _("Кому / куда в Snipe-IT"), "fieldtype": "Data", "width": 170},
		{"fieldname": "admin_name", "label": _("Кто сделал"), "fieldtype": "Data", "width": 150},
		{"fieldname": "note", "label": _("Заметка"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "asset",
			"label": _("Карточка"),
			"fieldtype": "Link",
			"options": "IT Asset",
			"width": 120,
		},
		{
			"fieldname": "person",
			"label": _("Карточка сотрудника"),
			"fieldtype": "Link",
			"options": "Person",
			"width": 140,
		},
	], rows
