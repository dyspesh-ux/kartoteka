import frappe


def execute():
	"""HR Source becomes Info Base: the registry of all 1C bases (ZUP and others).

	Runs before the model sync: the table, links and data are kept, only the DocType is renamed.
	Passwords live in __Auth under the DocType name, rename_doc does not move them.
	"""
	if frappe.db.exists("DocType", "HR Source") and not frappe.db.exists("DocType", "Info Base"):
		frappe.rename_doc("DocType", "HR Source", "Info Base", force=True)
	frappe.db.sql("update `__Auth` set doctype = 'Info Base' where doctype = 'HR Source'")
