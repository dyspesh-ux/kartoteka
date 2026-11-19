// List view of Diadoc organizations: the upload does not need a card — the button takes the
// export, the organizations in the file are created automatically.
frappe.listview_settings["Diadoc Organization"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Загрузить выгрузку"), () => {
			const input = document.createElement("input");
			input.type = "file";
			input.accept = ".xlsx";
			input.onchange = async () => {
				const file = input.files[0];
				if (!file) return;
				const bytes = new Uint8Array(await file.arrayBuffer());
				let binary = "";
				for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
				frappe.call({
					method: "access_registry.diadoc.api.upload",
					args: { content: btoa(binary), file_name: file.name },
					freeze: true,
					freeze_message: __("Загружаю выгрузку Диадок…"),
					callback(r) {
						const result = r.message || {};
						if (result.status === "Успех") {
							const orgs = Object.keys((result.stats || {}).organizations || {}).join(", ");
							frappe.show_alert({
								message: __("Загрузка выполнена: {0}. Организации: {1}", [result.log, orgs]),
								indicator: "green",
							});
						} else {
							frappe.msgprint({
								title: __("Загрузка Диадок: {0}", [result.status]),
								indicator: "red",
								message: __("Журнал: {0}. Откройте Sync Log для подробностей.", [result.log]),
							});
						}
						listview.refresh();
					},
					error(r) {
						frappe.msgprint({
							title: __("Загрузка не выполнена"),
							indicator: "red",
							message: (r && r.message) || __("Неизвестная ошибка — посмотрите консоль браузера"),
						});
					},
				});
			};
			input.click();
		});
	},
};
