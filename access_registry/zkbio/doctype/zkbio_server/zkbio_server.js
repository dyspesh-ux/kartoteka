// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("ZKBio Server", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Проверить подключение"), () =>
			frm.call("test_connection").then((r) => r.message && frappe.msgprint(r.message))
		);
		frm.add_custom_button(__("Синхронизировать сейчас"), () =>
			frm
				.call("sync_now")
				.then((r) => r.message && frappe.show_alert({ message: r.message, indicator: "green" }))
		);
		frm.add_custom_button(__("Перечитать за N дней"), () =>
			frappe.prompt(
				{
					fieldname: "days",
					fieldtype: "Int",
					label: __("За сколько дней перечитать события"),
					default: 7,
					reqd: 1,
				},
				({ days }) => frm.call("refetch_events", { days }).then((r) => r.message && frappe.msgprint(r.message)),
				__("Перечитать события"),
				__("Сдвинуть окно")
			)
		);
		frm.add_custom_button(
			__("Персонал СКУД"),
			() => frappe.set_route("List", "ZKBio Person", { server: frm.doc.name }),
			__("Открыть")
		);
		frm.add_custom_button(
			__("События СКУД"),
			() => frappe.set_route("List", "ZKBio Event", { server: frm.doc.name }),
			__("Открыть")
		);
		frm.add_custom_button(
			__("Журнал синхронизаций"),
			() => frappe.set_route("List", "Sync Log", { kind: "СКУД" }),
			__("Открыть")
		);
		frm.dashboard.set_headline(
			__("Реестр только читает PostgreSQL СКУД. Учётка не должна иметь прав на запись."),
			"green"
		);
	},
});
