// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IT Asset Movements"] = {
	filters: [
		{ fieldname: "from_date", label: __("С"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
		{ fieldname: "to_date", label: __("По"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "action", label: __("Действие"), fieldtype: "Select", options: ["", "Выдача", "Возврат", "Аудит", "Изменение", "Создание", "Удаление"] },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "only_handovers", label: __("Только выдачи и возвраты"), fieldtype: "Check" },
	],
};
