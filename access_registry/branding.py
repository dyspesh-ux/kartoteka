"""The registry under its own name: title, logo, menu items and site settings without the platform's.

Runs after install and after every migrate. Values an administrator has set are kept: only empty
settings or the platform's defaults are replaced.
"""

import frappe

from access_registry import app_access

APP_NAME = "Реестр доступа"
LOGO = "/assets/access_registry/images/registry-logo.svg"
PLATFORM_DEFAULTS = {"", None, "Frappe", "Frappe Framework"}
APP_ROUTE = "/registry"
APP_ITEM = "Приложение реестра"
# items of the desk menus that lead to the platform's site, support and «about»
HIDDEN_HELP_ITEMS = {
	"About",
	"Frappe Support",
	"Documentation",
	"User Forum",
	"Frappe School",
	"Report an Issue",
}
HIDDEN_USER_ITEMS = {"Apps", "View Website"}


def apply():
	_settings()
	_navbar()


def _set_if_default(doctype: str, field: str, value):
	meta = frappe.get_meta(doctype)
	if not meta.has_field(field):
		return
	if frappe.db.get_single_value(doctype, field) in PLATFORM_DEFAULTS:
		frappe.db.set_single_value(doctype, field, value)


def _settings():
	_set_if_default("System Settings", "app_name", APP_NAME)
	for field, value in (
		("app_name", APP_NAME),
		("favicon", LOGO),
		("splash_image", LOGO),
		("footer_powered", APP_NAME),
		# the top bar of the service pages (not found, messages, password change)
		(
			"brand_html",
			f'<img src="{LOGO}" alt="" style="height:24px;vertical-align:middle;margin-right:8px">{APP_NAME}',
		),
		# the start page of the site is the app (people who open the bare address get there)
		("home_page", APP_ROUTE.strip("/")),
	):
		_set_if_default("Website Settings", field, value)
	# no self sign-up: users are created by the administrators (or come through SSO)
	frappe.db.set_single_value("Website Settings", "disable_signup", 1)
	frappe.clear_cache()


def _navbar():
	navbar = frappe.get_single("Navbar Settings")
	changed = False
	if not any(i.route == APP_ROUTE for i in navbar.settings_dropdown):
		item = navbar.append(
			"settings_dropdown", {"item_label": APP_ITEM, "item_type": "Route", "route": APP_ROUTE}
		)
		# first in the user menu
		navbar.settings_dropdown.remove(item)
		navbar.settings_dropdown.insert(0, item)
		for i, row in enumerate(navbar.settings_dropdown, 1):
			row.idx = i
		changed = True
	for table, labels in (("help_dropdown", HIDDEN_HELP_ITEMS), ("settings_dropdown", HIDDEN_USER_ITEMS)):
		for row in navbar.get(table):
			if row.item_label in labels and not row.hidden:
				row.hidden = 1
				changed = True
	if changed:
		navbar.save(ignore_permissions=True)


def boot_session(bootinfo):
	"""The desk shows the link to the app only to those who may open it."""
	bootinfo.registry_app = {"can_open": app_access.can_open_app(), "route": APP_ROUTE, "title": APP_NAME}
