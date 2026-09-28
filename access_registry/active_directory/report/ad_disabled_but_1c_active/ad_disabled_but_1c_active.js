// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["AD Disabled But 1C Active"] = {
	filters: [
		{ fieldname: "domain", label: __("Домен"), fieldtype: "Link", options: "AD Domain" },
	],
};
