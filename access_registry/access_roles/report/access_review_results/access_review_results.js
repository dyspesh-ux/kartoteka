// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Access Review Results"] = {
	filters: [
		{ fieldname: "access_review", label: __("Пересмотр"), fieldtype: "Link", options: "Access Review", reqd: 1 },
		{ fieldname: "decision", label: __("Решение"), fieldtype: "Select", options: ["", "Оставить", "Отозвать", "Без решения"] },
		{ fieldname: "reviewer_user", label: __("Проверяющий"), fieldtype: "Link", options: "User" },
	],
};
