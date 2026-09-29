// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Share Permission Issues"] = {
	filters: [
		{ fieldname: "server", label: __("Сервер"), fieldtype: "Link", options: "File Server" },
		{ fieldname: "issue", label: __("Замечание содержит"), fieldtype: "Data" },
		{ fieldname: "show_suppressed", label: __("Показать погашенные"), fieldtype: "Check" },
	],
};
