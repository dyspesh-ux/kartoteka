// Upload button of a Diadoc organization card.
frappe.ui.form.on("Diadoc Organization", {
	refresh(frm) {
		frm.add_custom_button(__("Загрузить выгрузку"), () => {
			frm.call("load_file").then((r) => {
				const result = r.message || {};
				if (result.status === "Успех") {
					frappe.show_alert({ message: __("Загрузка Диадок выполнена: {0}", [result.log]), indicator: "green" });
					frm.reload_doc();
				} else {
					frappe.msgprint({
						title: __("Загрузка Диадок: {0}", [result.status]),
						indicator: "red",
						message: __("Журнал: {0}. Откройте Sync Log для подробностей.", [result.log]),
					});
				}
			});
		}, __("Действия"));
	},
});
