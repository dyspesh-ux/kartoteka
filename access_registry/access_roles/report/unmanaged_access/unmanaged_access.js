// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Unmanaged Access"] = {
	filters: [
		{ fieldname: "system", label: __("Система"), fieldtype: "Select", options: ["", "1С", "Active Directory", "Битрикс24", "Другое"] },
		{ fieldname: "min_holders", label: __("Есть не меньше чем у"), fieldtype: "Int", default: 1 },
	],
	get_datatable_options(options) {
		return Object.assign(options, { checkboxColumn: true });
	},
	onload(report) {
		report.page.add_inner_button(__("Добавить отмеченные в каталог"), () => {
			const keys = report.datatable.rowmanager.getCheckedRows().map((i) => report.data[i].key);
			if (!keys.length) return frappe.msgprint(__("Отметьте доступы галочками слева."));
			frappe
				.call({ method: "access_registry.access_roles.api.add_to_catalog", args: { keys } })
				.then(() => {
					frappe.show_alert({ message: __("Добавлено в каталог: {0}", [keys.length]), indicator: "green" });
					report.refresh();
				});
		});
	},
};
