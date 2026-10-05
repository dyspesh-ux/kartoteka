// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Registry Digest", {
	refresh(frm) {
		frm.add_custom_button(__("Предпросмотр"), () =>
			frm.call("preview").then((r) => r.message && frappe.set_route("Form", "Registry Digest Log", r.message))
		);
		frm.add_custom_button(__("Отправить сейчас"), () =>
			frappe.confirm(__("Отправить сводку получателям сейчас?"), () =>
				frm.call("send_now").then((r) => {
					const m = r.message || {};
					frappe.show_alert({ message: `${m.status}${m.error ? ": " + m.error : ""}`, indicator: m.status === "Отправлено" ? "green" : "gray" });
					frm.reload_doc();
				})
			)
		);
		frm.add_custom_button(__("Журнал сводок"), () => frappe.set_route("List", "Registry Digest Log"));
		frm.dashboard.set_headline(
			__("Письма уходят через почтовую учётную запись админки («Учетная запись электронной почты» с отметкой «Использовать для исходящих»). Без него сводка останется в очереди писем."),
			"gray"
		);
	},
});
