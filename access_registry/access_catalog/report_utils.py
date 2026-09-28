"""Shared query for the IB User list reports."""

import frappe
from frappe import _

USER_COLUMNS = [
	{
		"fieldname": "name",
		"label": _("Пользователь 1С"),
		"fieldtype": "Link",
		"options": "IB User",
		"width": 260,
	},
	{"fieldname": "base_code", "label": _("База"), "fieldtype": "Link", "options": "Info Base", "width": 80},
	{"fieldname": "base_configuration", "label": _("Конфигурация"), "fieldtype": "Data", "width": 110},
	{"fieldname": "login", "label": _("Логин"), "fieldtype": "Data", "width": 160},
	{"fieldname": "ad_login", "label": _("Логин AD"), "fieldtype": "Data", "width": 130},
	{"fieldname": "login_allowed", "label": _("Вход разрешён"), "fieldtype": "Check", "width": 100},
	{"fieldname": "invalid", "label": _("Недействителен"), "fieldtype": "Check", "width": 100},
	{"fieldname": "department_name", "label": _("Подразделение"), "fieldtype": "Data", "width": 180},
]
USER_FIELDS = (
	"u.name, u.user_name, u.base_code, u.login, u.ad_login, u.login_allowed, u.invalid, u.department_name"
)


def user_report(filters, where: str, extra_columns=(), extra_fields="", join="", params=None):
	filters = frappe._dict(filters or {})
	params = dict(params or {})
	conditions = ["u.missing_in_source = 0", f"({where})"]
	if filters.get("base_code"):
		conditions.append("u.base_code = %(base_code)s")
		params["base_code"] = filters.base_code
	if filters.get("configuration"):
		conditions.append("u.base_configuration = %(configuration)s")
		params["configuration"] = filters.configuration
	if not filters.get("include_invalid"):
		conditions.append("u.invalid = 0")
	fields = USER_FIELDS + (", " + extra_fields if extra_fields else "")
	data = frappe.db.sql(
		f"""select {fields} from `tabIB User` u {join}
		where {" and ".join(conditions)} order by u.user_name""",
		params,
		as_dict=True,
	)
	return USER_COLUMNS + list(extra_columns), data
