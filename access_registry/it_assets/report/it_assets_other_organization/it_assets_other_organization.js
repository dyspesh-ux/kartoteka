// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["IT Assets Other Organization"] = {
	filters: [
		{ fieldname: "server", label: __("Сервер Snipe-IT"), fieldtype: "Link", options: "Snipe-IT Server" },
		{ fieldname: "company_org", label: __("Куплена на"), fieldtype: "Data" },
	],
};
