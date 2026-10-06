// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Snipe-IT Server", {
	refresh(frm) {
		if (frm.is_new()) return;
		const call = (method) =>
			frappe.call({ method: `access_registry.it_assets.sync.${method}`, args: { server: frm.doc.name } });
		frm.add_custom_button(__("Проверить подключение"), () =>
			call("test_connection").then((r) =>
				frappe.msgprint(__("Подключение есть: техники {0}, пользователей {1}.", [r.message.hardware, r.message.users]))
			)
		);
		frm.add_custom_button(__("Загрузить сейчас"), () =>
			call("sync_now").then(() =>
				frappe.show_alert({ message: __("Загрузка поставлена в очередь"), indicator: "green" })
			)
		);
		const open = (label, doctype, filters) =>
			frm.add_custom_button(label, () => frappe.set_route("List", doctype, filters), __("Открыть"));
		open(__("Техника"), "IT Asset", { server: frm.doc.name });
		open(__("Пользователи"), "Snipe-IT User", { server: frm.doc.name });
		open(__("Журнал выдач"), "IT Asset Event", { server: frm.doc.name });
		open(__("Журнал синхронизаций"), "Sync Log", { kind: "Snipe-IT" });
		frm.dashboard.set_headline(
			__("Реестр только читает Snipe-IT: технику, пользователей и журнал выдач. Ничего не меняет."),
			"green"
		);
	},
});
