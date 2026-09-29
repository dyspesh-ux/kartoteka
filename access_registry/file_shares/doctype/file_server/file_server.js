// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("File Server", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Загрузить из файла"), () =>
			frm.call("load_file").then((r) => {
				const m = r.message;
				if (!m) return;
				frappe.show_alert({ message: `${m.status}: ${m.log}`, indicator: m.status === "Успех" ? "green" : "red" });
				frm.reload_doc();
			})
		);
		const open = (label, doctype, filters) =>
			frm.add_custom_button(label, () => frappe.set_route("List", doctype, filters), __("Открыть"));
		open(__("Общие папки"), "File Share", { server: frm.doc.name });
		open(__("Права папок"), "Folder ACL", { server: frm.doc.name, missing_in_source: 0 });
		open(__("Журнал синхронизаций"), "Sync Log", { kind: "Файловый сервер" });
		frm.add_custom_button(
			__("Кто имеет доступ"),
			() => frappe.set_route("query-report", "Share Access", { server: frm.doc.name }),
			__("Открыть")
		);
		frm.add_custom_button(
			__("Замечания по правам"),
			() => frappe.set_route("query-report", "Share Permission Issues", { server: frm.doc.name }),
			__("Открыть")
		);
		frm.dashboard.set_headline(
			__("Права присылает сборщик synology/registry_collect.sh (SERVER_CODE={0}). Реестр только читает, в NAS ничего не пишет.", [
				frm.doc.server_code,
			]),
			"blue"
		);
	},
});
