// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("IB User", {
	refresh(frm) {
		if (frm.doc.missing_in_source) {
			frm.dashboard.set_headline(__("Пользователя нет в последнем снимке 1С."), "gray");
		}
		if (frm.doc.has_extra_roles) {
			frm.dashboard.add_indicator(__("Есть роли в обход профилей"), "gray");
		}
		if (frm.doc.person) {
			frappe.db.get_value("Person", frm.doc.person, "status").then(({ message }) => {
				if (message && message.status) {
					const color = message.status === "Работает" ? "green" : "gray";
					frm.dashboard.add_indicator(__("Сотрудник: {0}", [message.status]), color);
				}
			});
		} else if (frm.doc.login_allowed) {
			frm.dashboard.add_indicator(
				__("Не привязан к сотруднику: {0}", [frm.doc.person_link_note || "—"]),
				"gray"
			);
		}
	},
});
