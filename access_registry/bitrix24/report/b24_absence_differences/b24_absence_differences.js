// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["B24 Absence Differences"] = {
	filters: [
		{ fieldname: "portal", label: __("Портал"), fieldtype: "Link", options: "B24 Portal" },
		{ fieldname: "days_back", label: __("Дней назад"), fieldtype: "Int", default: 30 },
		{ fieldname: "days_ahead", label: __("Дней вперёд"), fieldtype: "Int", default: 60 },
	],
};
