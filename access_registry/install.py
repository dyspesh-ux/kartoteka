import frappe

from access_registry.settings import DEFAULT_SYNC_USERS
from access_registry.sync.departments import ensure_root

# Roles of the registry users (see docs/roles-and-processes.md → «Кто работает с реестром»).
REGISTRY_ROLES = {
	"Registry Admin": "Администратор реестра: источники, загрузки, настройки, все данные",
	"Registry Auditor": "Аудитор (ИБ, служба безопасности, внутренний аудит): читает всё, включая кадровые данные",
	"Access Role Manager": "Владелец ролевой модели: права доступа, роли, исключения, конфликты полномочий",
	"Process Manager": "Методолог процессов: бизнес-процессы, роли процессов, участники",
	"Access Catalog Viewer": "Контролёр прав (главный бухгалтер, руководители): доступы и отчёты без персональных данных",
	"Access Reviewer": "Проверяющий в пересмотре доступа (руководитель): только свои задания в /registry",
	"1C Sync": "Технический пользователь внешнего транспорта (n8n): только методы импорта",
}
CATALOG_ROLES = tuple(REGISTRY_ROLES)
# Reviewers work only in /registry: they may be website users without a desk licence.
NO_DESK_ROLES = ("Access Reviewer",)


def after_install():
	create_roles()
	create_sync_users()
	ensure_root()
	frappe.db.commit()


def after_migrate():
	sync_workspace()
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
	for role in REGISTRY_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": int(role not in NO_DESK_ROLES)}
			).insert(ignore_permissions=True)


WORKSPACE = "Access Registry"
LEGACY_WORKSPACES = ("Кадры ЗУП",)


def sync_workspace():
	"""Keeps the app's workspace exactly as shipped in the repository.

	Frappe re-imports a workspace file only when the database copy is older than the file, so a copy
	touched on the site (or left from an earlier version) silently stays. The workspace is part of the
	app: it is re-imported on every migrate, and earlier interim names are removed.
	"""
	from frappe.modules.import_file import import_file

	for name in LEGACY_WORKSPACES:
		if frappe.db.exists("Workspace", name):
			frappe.delete_doc("Workspace", name, ignore_permissions=True, force=True)
	import_file("Access Registry", "Workspace", WORKSPACE, force=True)
	frappe.clear_cache()
