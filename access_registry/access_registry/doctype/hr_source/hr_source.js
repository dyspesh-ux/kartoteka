// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("HR Source", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Синхронизировать сейчас"), () => {
			frm.call("sync_now").then((r) => {
				if (r.message) frappe.show_alert({ message: r.message, indicator: "green" });
			});
		});
		frm.add_custom_button(__("Журнал синхронизаций"), () => {
			frappe.set_route("List", "Sync Log", { source: frm.doc.name });
		});
		if (frm.doc.itaccess_enabled) {
			const call = (method) =>
				frm.call(method).then((r) => {
					if (r.message) frappe.show_alert({ message: r.message, indicator: "green" });
				});
			frm.add_custom_button(__("Загрузить права сейчас"), () => call("load_rights_now"), __("Права 1С"));
			frm.add_custom_button(__("Загрузить журнал 1С сейчас"), () => call("load_log_now"), __("Права 1С"));
			frm.add_custom_button(
				__("Пользователи 1С"),
				() => frappe.set_route("List", "ZUP User", { base_code: frm.doc.name }),
				__("Права 1С")
			);
		}
	},
});
