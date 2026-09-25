import frappe


def execute():
	"""The workspace was first shipped as «Access Registry» with the title «Кадры ЗУП».

	Frappe v15 routes a workspace by the slug of its title but registers the page by the slug
	of its name, so the two must match: the workspace is now named «Кадры ЗУП».
	"""
	if frappe.db.exists("Workspace", "Access Registry"):
		frappe.delete_doc("Workspace", "Access Registry", ignore_permissions=True, force=True)
