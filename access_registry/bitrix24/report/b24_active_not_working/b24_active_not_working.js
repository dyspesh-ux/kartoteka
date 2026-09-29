// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["B24 Active Not Working"] = {
	filters: [
		{ fieldname: "portal", label: __("Портал"), fieldtype: "Link", options: "B24 Portal" },
		{ fieldname: "show_suppressed", label: __("Показать погашенные"), fieldtype: "Check" },
	],
};
