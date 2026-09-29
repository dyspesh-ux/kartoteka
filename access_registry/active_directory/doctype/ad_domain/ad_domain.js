// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("AD Domain", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Проверить подключение"), () =>
			frm.call("test_connection").then((r) => r.message && frappe.msgprint(r.message))
		);
		frm.add_custom_button(__("Загрузить сейчас"), () =>
			frm.call("sync_now").then((r) => r.message && frappe.show_alert({ message: r.message, indicator: "green" }))
		);
		frm.add_custom_button(__("Учётки"), () => frappe.set_route("List", "AD Account", { domain: frm.doc.name }), __("Открыть"));
		frm.add_custom_button(__("Группы"), () => frappe.set_route("List", "AD Group", { domain: frm.doc.name }), __("Открыть"));
		frm.add_custom_button(
			__("Журнал синхронизаций"),
			() => frappe.set_route("List", "Sync Log", { kind: "AD" }),
			__("Открыть")
		);
		frm.dashboard.set_headline(
			__("Реестр только читает AD. Учётка для подключения не должна иметь прав на запись."),
			"green"
		);
	},
});
