// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IB Extra Roles"] = {
	filters: [
		{ fieldname: "base_code", label: __("База"), fieldtype: "Link", options: "Info Base" },
		{ fieldname: "configuration", label: __("Конфигурация"), fieldtype: "Select", options: ["", "ЗУП", "Бухгалтерия", "Другая"] },
		{ fieldname: "include_invalid", label: __("Показывать недействительных"), fieldtype: "Check" },
		{ fieldname: "show_suppressed", label: __("Показать погашенные"), fieldtype: "Check" },
	],
};
