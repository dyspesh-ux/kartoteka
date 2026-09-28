// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["SoD Conflicts"] = {
	filters: [
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "rule", label: __("Правило"), fieldtype: "Link", options: "SoD Rule" },
	],
};
