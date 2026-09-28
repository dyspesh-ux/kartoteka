// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IB Login Not Working"] = {
	filters: [
		{ fieldname: "base_code", label: __("База"), fieldtype: "Link", options: "Info Base" },
		{ fieldname: "configuration", label: __("Конфигурация"), fieldtype: "Select", options: ["", "ЗУП", "Бухгалтерия", "Другая"] },
	],
};
