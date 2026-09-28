"""Who may do what in the registry (roles are created by install.create_roles)."""

import frappe
from frappe import _

ADMINS = ("System Manager", "Registry Admin")
AUDITOR = "Registry Auditor"
ROLE_MANAGER = "Access Role Manager"
PROCESS_MANAGER = "Process Manager"
VIEWER = "Access Catalog Viewer"

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
