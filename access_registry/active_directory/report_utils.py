"""Shared query for the AD Account list reports."""

import frappe
from frappe import _

ACCOUNT_COLUMNS = [
	{"fieldname": "display_name", "label": _("Учётка"), "fieldtype": "Data", "width": 240},
	{"fieldname": "sam_account_name", "label": _("Логин"), "fieldtype": "Data", "width": 140},
	{"fieldname": "domain", "label": _("Домен"), "fieldtype": "Link", "options": "AD Domain", "width": 80},
	{"fieldname": "enabled", "label": _("Включена"), "fieldtype": "Check", "width": 80},
	{"fieldname": "last_logon", "label": _("Последний вход"), "fieldtype": "Datetime", "width": 150},
	{"fieldname": "ou", "label": _("OU"), "fieldtype": "Data", "width": 220},
]
ACCOUNT_FIELDS = "a.name, a.display_name, a.sam_account_name, a.domain, a.enabled, a.last_logon, a.ou"
LINK_COLUMN = {
	"fieldname": "name",
	"label": _("Карточка"),
	"fieldtype": "Link",
	"options": "AD Account",
	"width": 140,
}


def account_report(
	filters, where, extra_columns=(), extra_fields="", join="", params=None, order="a.display_name"
):
	filters = frappe._dict(filters or {})
	params = dict(params or {})
	conditions = ["a.missing_in_source = 0", f"({where})"]
	if filters.get("domain"):
		conditions.append("a.domain = %(domain)s")
		params["domain"] = filters.domain
	if filters.get("ou"):
		conditions.append("a.ou like %(ou)s")
		params["ou"] = f"%{filters.ou}%"
	fields = ACCOUNT_FIELDS + (", " + extra_fields if extra_fields else "")
	data = frappe.db.sql(
		f"""select {fields} from `tabAD Account` a {join}
		where {" and ".join(conditions)} order by {order}""",
		params,
		as_dict=True,
	)
	return ACCOUNT_COLUMNS + list(extra_columns) + [LINK_COLUMN], data
