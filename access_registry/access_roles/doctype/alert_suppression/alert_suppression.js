// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Alert Suppression", {
	refresh(frm) {
		frm.disable_save();
		frm.dashboard.set_headline(
			__("Запись журнала. Погасить или вернуть замечание можно в приложении реестра: «Контроль»."),
			frm.doc.status === "Погашено" ? "gray" : "green"
		);
		frm.add_custom_button(__("Открыть «Контроль»"), () => window.open(`/registry#/control/${frm.doc.alert_kind || ""}`));
	},
});
