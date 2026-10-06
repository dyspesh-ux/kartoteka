// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("B24 Smart Process", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Загрузить сейчас"), () =>
			frm.call("sync_now").then((r) => r.message && frappe.show_alert({ message: r.message, indicator: "green" }))
		);
		frm.add_custom_button(__("Заявки"), () => frappe.set_route("List", "B24 Smart Item", { smart_process: frm.doc.name }), __("Открыть"));
		frm.add_custom_button(__("Журнал синхронизаций"), () => frappe.set_route("List", "Sync Log", { kind: "Смарт-процессы Битрикс24" }), __("Открыть"));
		frm.add_custom_button(__("Техподдержка в приложении"), () => window.open("/registry#/support"), __("Открыть"));
	},
});
