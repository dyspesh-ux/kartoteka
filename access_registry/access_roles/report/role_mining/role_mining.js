// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["Role Mining"] = {
	filters: [
		{ fieldname: "min_people", label: __("Сотрудников в должности не меньше"), fieldtype: "Int", default: 3 },
		{ fieldname: "threshold", label: __("Доступ есть у, %"), fieldtype: "Int", default: 80 },
		{ fieldname: "position", label: __("Должность содержит"), fieldtype: "Data" },
		{ fieldname: "only_suggested", label: __("Только предлагаемые в роль"), fieldtype: "Check", default: 1 },
	],
	onload(report) {
		report.page.add_inner_button(__("Создать черновики ролей"), () => {
			const f = report.get_values() || {};
			frappe.confirm(
				__("Для каждой должности без роли будет создан черновик роли с доступами, которые есть не меньше чем у {0}% сотрудников. Продолжить?", [f.threshold || 80]),
				() =>
					frappe
						.call({
							method: "access_registry.access_roles.api.create_draft_roles",
							args: { min_people: f.min_people, threshold: f.threshold },
						})
						.then((r) => {
							frappe.msgprint(r.message.message);
							report.refresh();
						})
			);
		});
	},
};
