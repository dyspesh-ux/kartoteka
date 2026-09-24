# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe.utils.nestedset import NestedSet


class HRDepartment(NestedSet):
	nsm_parent_field = "parent_hr_department"

	def validate(self):
		self.is_group = 1
		self.update_head()

	def update_head(self):
		"""The head from ZUP wins over the manual one; the candidate is never used automatically."""
		if self.zup_head:
			self.head, self.head_source = self.zup_head, "ЗУП"
		elif self.manual_head:
			self.head, self.head_source = self.manual_head, "Вручную"
		else:
			self.head, self.head_source = None, ""
		self.head_conflict = int(
			bool(self.zup_head and self.manual_head and self.zup_head != self.manual_head)
		)


@frappe.whitelist()
def get_children(doctype, parent="", **filters):
	"""Tree view: children ordered by title instead of the GUID-based name."""
	return frappe.get_list(
		"HR Department",
		fields=["name as value", "title", "is_group as expandable"],
		filters=[["ifnull(`parent_hr_department`, '')", "=", parent or ""]],
		order_by="node_type desc, title asc",
	)
