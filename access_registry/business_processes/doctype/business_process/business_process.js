// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Business Process", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Добавить роль"), () =>
			frappe.new_doc("Process Role", { business_process: frm.doc.name })
		);
		frm.add_custom_button(__("Участники"), () =>
			frappe.set_route("query-report", "Process Participants", { process: frm.doc.name })
		, __("Открыть"));
		frm.add_custom_button(__("Риски"), () =>
			frappe.set_route("query-report", "Process Continuity", { process: frm.doc.name, only_problems: 0 })
		, __("Открыть"));
		frm.add_custom_button(__("В приложении"), () => window.open(`/registry#/process/${encodeURIComponent(frm.doc.name)}`), __("Открыть"));
	},
});
