// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Process Import", {
	refresh(frm) {
		frm.add_custom_button(__("Скачать шаблон"), () => {
			window.open("/api/method/access_registry.business_processes.api.download_template");
		});
		if (frm.is_new() || !frm.doc.import_file || frm.doc.status === "Загружен") return;
		const run = (method) =>
			frm.call(method).then(() => {
				frm.reload_doc();
				frappe.show_alert({
					message: frm.doc.summary || __("Готово"),
					indicator: frm.doc.status === "Есть ошибки" ? "gray" : "green",
				});
			});
		frm.add_custom_button(__("Проверить"), () => run("check"));
		frm.add_custom_button(__("Загрузить"), () => run("load")).addClass("btn-primary");
		if (frm.doc.status === "Есть ошибки")
			frm.dashboard.set_headline(__("В файле есть ошибки — ничего не загружено. Исправьте строки из списка ниже и прикрепите файл заново."), "gray");
		else if (frm.doc.status === "Проверен")
			frm.dashboard.set_headline(__("Проверка прошла. Нажмите «Загрузить»."), "green");
	},
});
