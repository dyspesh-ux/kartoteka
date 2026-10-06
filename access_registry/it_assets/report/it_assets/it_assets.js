// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IT Assets"] = {
	filters: [
		{ fieldname: "server", label: __("Сервер Snipe-IT"), fieldtype: "Link", options: "Snipe-IT Server" },
		{ fieldname: "category", label: __("Категория"), fieldtype: "Data" },
		{ fieldname: "status_label", label: __("Статус"), fieldtype: "Data" },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "only_handed_out", label: __("Только выданные сотрудникам"), fieldtype: "Check" },
		{ fieldname: "only_not_working", label: __("Только у неработающих"), fieldtype: "Check" },
		{ fieldname: "include_archived", label: __("Показывать списанную"), fieldtype: "Check" },
	],
};
