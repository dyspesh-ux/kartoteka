// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IB Access Report"] = {
	filters: [
		{ fieldname: "base_code", label: __("База"), fieldtype: "Link", options: "Info Base" },
		{ fieldname: "configuration", label: __("Конфигурация"), fieldtype: "Select", options: ["", "ЗУП", "Бухгалтерия", "Другая"] },
		{ fieldname: "organization", label: __("Организация (осн. место работы)"), fieldtype: "Link", options: "HR Organization" },
		{ fieldname: "department", label: __("Подразделение"), fieldtype: "Link", options: "HR Department" },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "by_profile", label: __("По профилям"), fieldtype: "Check" },
		{ fieldname: "include_disabled", label: __("Показывать без права входа"), fieldtype: "Check" },
		{ fieldname: "only_unlinked", label: __("Только не привязанные к сотруднику"), fieldtype: "Check" },
	],
};
