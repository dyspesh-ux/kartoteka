// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["ZUP Login Not Working"] = {
	filters: [
		{ fieldname: "base_code", label: __("База"), fieldtype: "Link", options: "HR Source" },
	],
};
