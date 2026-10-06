// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("B24 Portal", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Проверить подключение"), () =>
			frm.call("test_connection").then((r) => r.message && frappe.msgprint(r.message))
		);
		frm.add_custom_button(__("Загрузить сейчас"), () =>
			frm.call("sync_now").then((r) => r.message && frappe.show_alert({ message: r.message, indicator: "green" }))
		);
		frm.add_custom_button(__("Структура смарт-процессов"), () =>
			window.open(
				`/api/method/access_registry.bitrix24.smart_structure.download?portal=${encodeURIComponent(frm.doc.name)}`
			)
		);
		const open = (label, doctype, filters) =>
			frm.add_custom_button(label, () => frappe.set_route("List", doctype, filters), __("Открыть"));
		open(__("Пользователи"), "B24 User", { portal: frm.doc.name });
		open(__("Подразделения"), "B24 Department", { portal: frm.doc.name });
		open(__("Группы и проекты"), "B24 Workgroup", { portal: frm.doc.name });
		open(__("Права на разделы"), "B24 Access Grant", { portal: frm.doc.name });
		open(__("Журнал записи"), "B24 Write Log", { portal: frm.doc.name });
		open(__("Журнал синхронизаций"), "Sync Log", { kind: "Битрикс24" });
		frm.dashboard.set_headline(
			frm.doc.write_enabled
				? __("После каждой загрузки реестр записывает в профили: {0}. Режим: {1}.", [
						[
							frm.doc.write_middle_name ? __("отчество") : "",
							frm.doc.write_birthday ? __("дату рождения") : "",
							frm.doc.person_uuid_field ? frm.doc.person_uuid_field : "",
						]
							.filter(Boolean)
							.join(", ") || __("ничего"),
						frm.doc.write_mode,
				  ])
				: __("Реестр только читает портал. Запись отчества и даты рождения включается в секции «Запись в Битрикс24»."),
			frm.doc.write_enabled ? "gray" : "green"
		);
	},
});
