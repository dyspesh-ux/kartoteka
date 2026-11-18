"""The «Диадок» section of the /registry app: organizations with upload freshness and their users."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

from access_registry import app_access as aa
from access_registry.registry.api import _check

USER_FIELDS = [
	"name",
	"organization",
	"full_name",
	"position",
	"login",
	"login_active",
	"is_admin",
	"blocked",
	"can_sign",
	"gov_key",
	"can_approve",
	"can_request_approve",
	"drafts",
	"can_delete",
	"payments",
	"counterparties",
	"missing_in_source",
	"person",
	"person_link_method",
	"person_link_note",
]


def _require_dd():
	_check("diadoc")
	if "dd" not in aa.systems():
		raise frappe.PermissionError(_("Система «Диадок» вам не открыта"))


@frappe.whitelist()
def diadoc_organizations() -> list[dict]:
	"""Every Diadoc organization with its last upload and the overdue flag."""
	_require_dd()
	rows = []
	for org in frappe.get_all(
		"Diadoc Organization",
		fields=[
			"name",
			"title",
			"last_upload",
			"last_status",
			"upload_reminder_days",
			"users_count",
			"linked_count",
		],
		order_by="title",
		limit_page_length=0,
	):
		stale_days = cint(org.upload_reminder_days) or 30
		overdue = not org.last_upload or getdate(org.last_upload) < getdate(add_days(today(), -stale_days))
		rows.append(
			{
				"name": org.name,
				"title": org.title or org.name,
				"last_upload": str(org.last_upload) if org.last_upload else None,
				"last_status": org.last_status,
				"upload_reminder_days": stale_days,
				"users_count": org.users_count or 0,
				"linked_count": org.linked_count or 0,
				"overdue": overdue,
			}
		)
	return rows


@frappe.whitelist()
def diadoc_users(organization: str) -> list[dict]:
	"""Users of one Diadoc organization with their right flags and the linked employee."""
	_require_dd()
	if not frappe.db.exists("Diadoc Organization", organization):
		frappe.throw(_("Организация Диадок {0} не найдена").format(organization), frappe.DoesNotExistError)
	users = frappe.get_all(
		"Diadoc User",
		filters={"organization": organization},
		fields=USER_FIELDS,
		order_by="full_name",
		limit_page_length=0,
	)
	names = {u.person for u in users if u.person}
	full_names = (
		dict(
			frappe.get_all(
				"Person",
				filters={"name": ["in", list(names) or [""]]},
				fields=["name", "full_name"],
				as_list=True,
			)
		)
		if names
		else {}
	)
	for user in users:
		user["person_name"] = full_names.get(user.person) if user.person else None
	return users
