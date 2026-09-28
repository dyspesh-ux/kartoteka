// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Process Participants"] = {
	filters: [
		{ fieldname: "process", label: __("Процесс"), fieldtype: "Link", options: "Business Process" },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "only_problems", label: __("Только неработающие и отсутствующие"), fieldtype: "Check" },
	],
};
