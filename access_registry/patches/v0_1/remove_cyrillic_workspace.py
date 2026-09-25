import frappe


def execute():
	"""Removes the short-lived «Кадры ЗУП» workspace: the workspace is «Access Registry» again.

	Frappe v15 routes a workspace by slug(title) and registers it by slug(name), so name and
	title must be equal; an English name keeps the URL /app/access-registry.
	"""
	if frappe.db.exists("Workspace", "Кадры ЗУП"):
		frappe.delete_doc("Workspace", "Кадры ЗУП", ignore_permissions=True, force=True)
