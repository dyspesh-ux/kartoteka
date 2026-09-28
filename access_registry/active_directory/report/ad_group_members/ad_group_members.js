// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["AD Group Members"] = {
	filters: [
		{ fieldname: "group", label: __("Группа"), fieldtype: "Link", options: "AD Group", reqd: 1 },
	],
};
