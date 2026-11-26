// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["ZKBio Attendance"] = {
	filters: [
		{ fieldname: "date_from", label: __("С даты"), fieldtype: "Date" },
		{ fieldname: "date_to", label: __("По дату"), fieldtype: "Date" },
		{ fieldname: "department", label: __("Подразделение"), fieldtype: "Link", options: "HR Department" },
		{ fieldname: "show_absent", label: __("Показывать отсутствующих"), fieldtype: "Check" },
		{ fieldname: "show_weekends", label: __("Показывать выходные"), fieldtype: "Check" },
	],
};
