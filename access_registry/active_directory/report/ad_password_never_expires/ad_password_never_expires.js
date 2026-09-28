// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["AD Password Never Expires"] = {
	filters: [
		{ fieldname: "domain", label: __("Домен"), fieldtype: "Link", options: "AD Domain" },
		{ fieldname: "ou", label: __("OU содержит"), fieldtype: "Data" },
	],
};
