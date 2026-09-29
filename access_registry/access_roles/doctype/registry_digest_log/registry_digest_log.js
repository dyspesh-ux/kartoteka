// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Registry Digest Log", {
	refresh(frm) {
		frm.disable_save();
		frm.get_field("letter").$wrapper.html(frm.doc.message_html || "");
	},
});
