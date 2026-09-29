// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["AD Inactive Accounts"] = {
	filters: [
		{ fieldname: "domain", label: __("Домен"), fieldtype: "Link", options: "AD Domain" },
		{ fieldname: "ou", label: __("OU содержит"), fieldtype: "Data" },
		{ fieldname: "days", label: __("Не входили дольше, дней"), fieldtype: "Int", default: 90 },
		{ fieldname: "show_suppressed", label: __("Показать погашенные"), fieldtype: "Check" },
	],
};
