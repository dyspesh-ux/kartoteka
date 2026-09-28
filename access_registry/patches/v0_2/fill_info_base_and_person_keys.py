import frappe

from access_registry.sync.normalize import normalize_name


def execute():
	# Every base registered before Info Base existed was a ZUP base (HR source).
	frappe.db.sql("update `tabInfo Base` set configuration = 'ЗУП' where ifnull(configuration, '') = ''")
	for name, full_name in frappe.get_all("Person", fields=["name", "full_name"], as_list=True):
		frappe.db.set_value("Person", name, "name_key", normalize_name(full_name), update_modified=False)
