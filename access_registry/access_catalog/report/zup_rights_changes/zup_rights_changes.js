// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["ZUP Rights Changes"] = {
	filters: [
		{ fieldname: "base_code", label: __("База"), fieldtype: "Link", options: "HR Source" },
		{ fieldname: "from_date", label: __("С"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -7) },
		{ fieldname: "to_date", label: __("По"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "who", label: __("Кто"), fieldtype: "Data" },
		{ fieldname: "object_type", label: __("Тип объекта"), fieldtype: "Select", options: ["", "Справочник.Пользователи", "Справочник.ГруппыПользователей", "Справочник.ГруппыДоступа", "Справочник.ПрофилиГруппДоступа"] },
	],
};
