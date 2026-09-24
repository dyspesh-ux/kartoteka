// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Person Merge Candidate", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Открыт") return;
		frm.add_custom_button(__("Склеить B в A"), () => {
			frappe.confirm(
				__("Все трудоустройства, события и ссылки человека B перейдут к A, B будет удалён. UUID A не изменится. Продолжить?"),
				() =>
					frm.call("merge").then((r) => {
						if (r.message) frappe.show_alert({ message: r.message, indicator: "green" });
						frm.reload_doc();
					})
			);
		});
		frm.add_custom_button(__("Это разные люди"), () => {
			frm.call("mark_different").then((r) => {
				if (r.message) frappe.show_alert({ message: r.message, indicator: "blue" });
				frm.reload_doc();
			});
		});
	},
});
