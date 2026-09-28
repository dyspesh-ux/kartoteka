// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["B24 Section Access"] = {
	filters: [
		{ fieldname: "portal", label: __("Портал"), fieldtype: "Link", options: "B24 Portal" },
		{ fieldname: "resource_type", label: __("Раздел"), fieldtype: "Select", options: ["", "CRM", "Смарт-процесс", "Диск", "Группа пользователей"] },
		{ fieldname: "resource", label: __("Ресурс содержит"), fieldtype: "Data" },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "only_not_working", label: __("Только неработающие"), fieldtype: "Check" },
		{ fieldname: "include_inactive", label: __("Включая неактивных в Битрикс24"), fieldtype: "Check" },
	],
};
