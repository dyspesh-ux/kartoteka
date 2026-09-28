// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("ZUP User", {
	refresh(frm) {
		if (frm.doc.missing_in_source) {
			frm.dashboard.set_headline(__("Пользователя нет в последнем снимке 1С."), "orange");
		}
		if (frm.doc.has_extra_roles) {
			frm.dashboard.add_indicator(__("Есть роли в обход профилей"), "red");
		}
		if (frm.doc.person) {
			frappe.db.get_value("Person", frm.doc.person, "status").then(({ message }) => {
				if (message && message.status) {
					const color = message.status === "Работает" ? "green" : "red";
					frm.dashboard.add_indicator(__("Человек: {0}", [message.status]), color);
				}
			});
		}
	},
});
