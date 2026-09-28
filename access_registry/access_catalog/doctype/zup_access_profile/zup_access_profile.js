// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.ui.form.on("ZUP Access Profile", {
	refresh(frm) {
		if (frm.is_new()) return;
		const open_users = () => {
			frappe.route_options = { "ZUP User Profile.profile": frm.doc.name };
			frappe.set_route("List", "ZUP User");
		};
		frm.add_custom_button(__("Пользователи с этим профилем"), open_users);
		frappe
			.xcall("frappe.client.get_count", {
				doctype: "ZUP User",
				filters: [
					["ZUP User Profile", "profile", "=", frm.doc.name],
					["ZUP User", "missing_in_source", "=", 0],
				],
			})
			.then((count) => {
				frm.dashboard.add_indicator(__("Пользователей с профилем: {0}", [count]), count ? "blue" : "gray");
			});
	},
});
