// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Share Access"] = {
	filters: [
		{ fieldname: "server", label: __("Сервер"), fieldtype: "Link", options: "File Server" },
		{ fieldname: "share", label: __("Общая папка"), fieldtype: "Link", options: "File Share" },
		{ fieldname: "path", label: __("Путь содержит"), fieldtype: "Data" },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "min_level", label: __("Доступ не ниже"), fieldtype: "Select", options: ["", "Чтение", "Изменение", "Полный доступ"] },
		{ fieldname: "only_not_working", label: __("Только неработающие и непривязанные"), fieldtype: "Check" },
	],
};
