// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["ZUP Profile Users"] = {
	filters: [
		{ fieldname: "profile", label: __("Профиль"), fieldtype: "Link", options: "ZUP Access Profile", reqd: 1 },
		{ fieldname: "include_invalid", label: __("Показывать недействительных"), fieldtype: "Check" },
	],
};
