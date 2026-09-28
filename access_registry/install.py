import frappe

from access_registry.settings import DEFAULT_SYNC_USERS
from access_registry.sync.departments import ensure_root

# 1C Sync: technical role of the API user (n8n) — only the import methods.
# Access Catalog Viewer: read access to the catalog of 1C user rights.
CATALOG_ROLES = ("1C Sync", "Access Catalog Viewer")


def after_install():
	create_roles()
	create_sync_users()
	ensure_root()
	frappe.db.commit()


def after_migrate():
	create_roles()
	create_sync_users()
	ensure_root()


def create_sync_users():
	"""Technical users on whose behalf the synchronisations write data.

	They have no password and no roles: the sync writes with ignore_permissions,
	the users only make the author of every change visible in the version history.
	"""
	users = set(DEFAULT_SYNC_USERS)
	configured = frappe.db.get_single_value("Access Registry Settings", "sync_user")
	if configured:
		users.add(configured)
	for email in sorted(users):
		if frappe.db.exists("User", email):
			continue
		user = frappe.new_doc("User")
		user.email = email
		user.first_name = email.split("@")[0]
		user.send_welcome_email = 0
		user.user_type = "Website User"
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)


def create_roles():
	for role in CATALOG_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)
