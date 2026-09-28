// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Process Role", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Добавить участника"), () =>
			frappe.new_doc("Process Participant", { process_role: frm.doc.name })
		);
	},
});
