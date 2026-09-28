// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["B24 Workgroup Members"] = {
	filters: [
		{ fieldname: "portal", label: __("Портал"), fieldtype: "Link", options: "B24 Portal" },
		{ fieldname: "group", label: __("Группа или проект"), fieldtype: "Link", options: "B24 Workgroup" },
		{ fieldname: "only_not_working", label: __("Только неработающие"), fieldtype: "Check" },
	],
};
