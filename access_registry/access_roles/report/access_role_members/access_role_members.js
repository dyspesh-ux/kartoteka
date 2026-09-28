// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Access Role Members"] = {
	filters: [
		{ fieldname: "access_role", label: __("Роль доступа"), fieldtype: "Link", options: "Access Role" },
	],
};
