"""Who may do what in the registry (roles are created by install.create_roles)."""

import frappe
from frappe import _

ADMINS = ("System Manager", "Registry Admin")
AUDITOR = "Registry Auditor"
ROLE_MANAGER = "Access Role Manager"
PROCESS_MANAGER = "Process Manager"
VIEWER = "Access Catalog Viewer"
REVIEWER = "Access Reviewer"

# Everybody who works with the registry data at all.
READERS = (*ADMINS, AUDITOR, ROLE_MANAGER, PROCESS_MANAGER, VIEWER)
# Personal data of employees (birth dates, HR statuses of dismissed people) and security findings.
PERSONAL_DATA = (*ADMINS, AUDITOR)


def has_any(*roles) -> bool:
	if frappe.session.user == "Administrator":
		return True
	return bool(set(frappe.get_roles()) & set(roles))


def require(*roles):
	"""Raises PermissionError unless the user has one of the roles (administrators always pass)."""
	if not has_any(*ADMINS, *roles):
		raise frappe.PermissionError(_("Недостаточно прав"))


def require_source_sender(doctype: str, name: str):
	"""When the source card names its technical API user, only that user (and administrators) may
	push data for it: a token of another collector cannot overwrite this source."""
	sender = frappe.db.get_value(doctype, name, "api_user")
	if sender and frappe.session.user != sender and not has_any(*ADMINS):
		raise frappe.PermissionError(_("Данные этого источника принимаются только от {0}").format(sender))
