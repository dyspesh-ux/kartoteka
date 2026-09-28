# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe.utils.nestedset import NestedSet


class BusinessProcess(NestedSet):
	nsm_parent_field = "parent_business_process"

	def validate(self):
		if self.level == "Группа процессов":
			self.is_group = 1
