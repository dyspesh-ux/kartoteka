// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Access Reconciliation"] = {
	filters: [
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "access_role", label: __("Роль доступа"), fieldtype: "Link", options: "Access Role" },
		{ fieldname: "status", label: __("Итог"), fieldtype: "Select", options: ["", "Не хватает", "Лишнее", "Лишнее: не работает", "Исключение", "Соответствует"] },
		{ fieldname: "system", label: __("Система"), fieldtype: "Select", options: ["", "1С", "Active Directory", "Битрикс24", "Другое"] },
		{ fieldname: "only_privileged", label: __("Только привилегированные"), fieldtype: "Check" },
		{ fieldname: "show_ok", label: __("Показывать соответствующие"), fieldtype: "Check" },
	],
};
