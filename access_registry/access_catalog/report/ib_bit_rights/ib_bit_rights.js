// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IB BIT Rights"] = {
	filters: [
		{ fieldname: "base_code", label: __("База"), fieldtype: "Link", options: "Info Base" },
		{ fieldname: "kind", label: __("Вид"), fieldtype: "Select", options: ["", "Виза", "Роль исполнителя", "Доступ к ЦФО"] },
		{ fieldname: "right_name", label: __("Виза или роль"), fieldtype: "Data" },
		{ fieldname: "cfo", label: __("ЦФО"), fieldtype: "Data" },
		{ fieldname: "person", label: __("Сотрудник"), fieldtype: "Link", options: "Person" },
		{ fieldname: "only_not_working", label: __("Только у неработающих"), fieldtype: "Check" },
		{ fieldname: "include_disabled", label: __("Показывать без права входа"), fieldtype: "Check" },
	],
};
