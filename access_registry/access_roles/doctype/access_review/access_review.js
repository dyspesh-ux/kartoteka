// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("Access Review", {
	refresh(frm) {
		if (frm.is_new()) return;
		const call = (method) =>
			frm.call(method).then((r) => {
				if (r.message) frappe.msgprint(r.message);
				frm.reload_doc();
			});
		if (frm.doc.status === "Черновик")
			frm.add_custom_button(__("Начать пересмотр"), () =>
				frappe.confirm(__("Реестр соберёт текущие доступы и раздаст их проверяющим. Начать?"), () => call("start"))
			).addClass("btn-primary");
		if (frm.doc.status === "Идёт") frm.add_custom_button(__("Завершить"), () => call("finish"));
		if (frm.doc.status !== "Черновик") {
			frm.add_custom_button(__("Результаты"), () =>
				frappe.set_route("query-report", "Access Review Results", { access_review: frm.doc.name })
			);
			frm.add_custom_button(__("В приложении"), () => window.open(`/registry#/review/${encodeURIComponent(frm.doc.name)}`));
			const done = frm.doc.items_total ? Math.round((100 * frm.doc.items_done) / frm.doc.items_total) : 0;
			frm.dashboard.set_headline(
				__("Решено {0}% ({1} из {2}), на отзыв {3}, без проверяющего {4}.", [
					done, frm.doc.items_done, frm.doc.items_total, frm.doc.items_revoke, frm.doc.items_unassigned,
				]),
				frm.doc.items_unassigned ? "orange" : "blue"
			);
		}
	},
});
