// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["ZKBio No Passes"] = {
	filters: [
		{
			fieldname: "date_from",
			label: __("С даты"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -7),
			reqd: 1,
		},
		{
			fieldname: "date_to",
			label: __("По дату"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{ fieldname: "department", label: __("Подразделение"), fieldtype: "Link", options: "HR Department" },
	],
};
