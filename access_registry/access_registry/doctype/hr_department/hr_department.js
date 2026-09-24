// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("HR Department", {
	refresh(frm) {
		if (frm.doc.head_candidate && !frm.doc.manual_head) {
			frm.add_custom_button(__("Принять кандидата"), () => {
				frm.set_value("manual_head", frm.doc.head_candidate);
				frm.save();
			});
		}
		if (frm.doc.head_conflict) {
			frm.dashboard.set_headline(
				__("Руководитель в ЗУП и указанный вручную различаются. Действует руководитель из ЗУП."),
				"orange"
			);
		}
	},
});
