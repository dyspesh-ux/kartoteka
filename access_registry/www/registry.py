"""The registry app for end users: /registry (a single page, data from access_registry.registry.api)."""

import frappe

from access_registry import app_access
from access_registry.permissions import has_any

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/registry"
		raise frappe.Redirect
	context.no_cache = 1
	context.allowed = app_access.can_open_app()  # registry roles or an access profile
	context.title = "Реестр доступа"
	context.desk = has_any("System Manager", "Registry Admin")
	# the admin (desk) opens for system users; website users (profiles only) do not get the link
	context.admin = frappe.get_cached_value("User", frappe.session.user, "user_type") == "System User"
