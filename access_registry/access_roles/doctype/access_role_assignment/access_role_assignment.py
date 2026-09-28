# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class AccessRoleAssignment(Document):
	def validate(self):
		if self.valid_from and self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("«По» раньше, чем «С»."))
		if self.is_new() or self.has_value_changed("access_role") or self.has_value_changed("valid_to"):
			self.approved_by = frappe.session.user
