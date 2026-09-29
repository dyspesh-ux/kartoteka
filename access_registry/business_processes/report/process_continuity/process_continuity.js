// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Process Continuity"] = {
	filters: [
		{ fieldname: "process", label: __("Процесс"), fieldtype: "Link", options: "Business Process" },
		{ fieldname: "only_problems", label: __("Только с рисками"), fieldtype: "Check", default: 1 },
		{ fieldname: "show_suppressed", label: __("Показать погашенные"), fieldtype: "Check" },
	],
};
