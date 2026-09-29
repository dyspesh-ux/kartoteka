// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Info Base", {
	refresh(frm) {
		if (frm.is_new()) return;
		const call = (method) =>
			frm.call(method).then((r) => {
				if (r.message) frappe.show_alert({ message: r.message, indicator: "green" });
			});

		if (frm.doc.configuration === "ЗУП") {
			frm.add_custom_button(__("Синхронизировать кадры"), () => call("sync_now"), __("Загрузить"));
		}
		if (frm.doc.itaccess_enabled) {
			frm.add_custom_button(__("Пользователи и права"), () => call("load_rights_now"), __("Загрузить"));
			frm.add_custom_button(__("Журнал изменений прав"), () => call("load_log_now"), __("Загрузить"));
			frm.add_custom_button(
				__("Пользователи базы"),
				() => frappe.set_route("List", "IB User", { base_code: frm.doc.name }),
				__("Открыть")
			);
		}
		frm.add_custom_button(
			__("Журнал синхронизаций"),
			() => frappe.set_route("List", "Sync Log", { source: frm.doc.name }),
			__("Открыть")
		);

		const role =
			frm.doc.configuration === "ЗУП"
				? __("Источник правды о сотрудниках, подразделениях и организациях. Отсюда же — пользователи и права этой базы.")
				: __("База без кадровых данных: загружаются только пользователи и права. Пользователи привязываются к сотрудникам из баз ЗУП.");
		frm.dashboard.set_headline(role, frm.doc.configuration === "ЗУП" ? "green" : "gray");
	},
});
