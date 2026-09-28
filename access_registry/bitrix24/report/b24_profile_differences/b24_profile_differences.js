// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["B24 Profile Differences"] = {
	filters: [
		{ fieldname: "portal", label: __("Портал"), fieldtype: "Link", options: "B24 Portal" },
		{ fieldname: "field", label: __("Что отличается"), fieldtype: "Select", options: ["", "Отчество", "Дата рождения", "Должность", "Подразделение"] },
	],
};
