// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Access Role", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Кто получает роль"), () =>
			frappe.set_route("query-report", "Access Role Members", { access_role: frm.doc.name })
		);
		frm.add_custom_button(__("Сверка по роли"), () =>
			frappe.set_route("query-report", "Access Reconciliation", { access_role: frm.doc.name })
		);
		frm.add_custom_button(__("Открыть в приложении"), () => window.open(`/registry#/role/${encodeURIComponent(frm.doc.name)}`));
		if (frm.doc.status === "Черновик")
			frm.dashboard.set_headline(__("Черновик: в сверке не участвует. Проверьте правила и права и переведите в «Действует»."), "orange");
	},
});
