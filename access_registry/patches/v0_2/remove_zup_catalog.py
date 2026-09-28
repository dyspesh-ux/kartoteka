import frappe

OLD_DOCTYPES = (
	"ZUP User",
	"ZUP User Profile",
	"ZUP User Role",
	"ZUP Access Profile",
	"ZUP Profile Role",
	"ZUP Audit Event",
)
OLD_REPORTS = (
	"ZUP Extra Roles",
	"ZUP Login Without Person",
	"ZUP IB Orphans",
	"ZUP All Organizations Access",
	"ZUP Login Not Working",
	"ZUP Profile Users",
	"ZUP Rights Changes",
)


def execute():
	"""The rights catalog is not ZUP-only any more: ZUP * DocTypes are replaced by IB *.

	The data is a mirror of 1C and is loaded again by the next rights snapshot.
	"""
	for report in OLD_REPORTS:
		if frappe.db.exists("Report", report):
			frappe.delete_doc("Report", report, force=True, ignore_permissions=True)
	for doctype in OLD_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
		# delete_doc keeps the table during migrate
		frappe.db.sql_ddl(f"drop table if exists `tab{doctype}`")
