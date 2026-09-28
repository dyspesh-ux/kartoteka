// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Entitlement", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("У кого есть"), () => window.open(`/registry#/entitlement/${encodeURIComponent(frm.doc.name)}`));
		frm.add_custom_button(__("Сверка по праву"), () =>
			frappe.set_route("query-report", "Access Reconciliation", { show_ok: 1, system: frm.doc.system })
		);
	},
});
