// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.treeview_settings["HR Department"] = {
	get_tree_nodes: "access_registry.access_registry.doctype.hr_department.hr_department.get_children",
	get_tree_root: false,
	root_label: "ROOT",
	disable_add_node: true,
	title: __("Подразделения"),
	breadcrumb: "Access Registry",
	get_label(node) {
		return node.title || node.label || node.data.value;
	},
	onload(treeview) {
		treeview.page.clear_primary_action && treeview.page.clear_primary_action();
	},
};
