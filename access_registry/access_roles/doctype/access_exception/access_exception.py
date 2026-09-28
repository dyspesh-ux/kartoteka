# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AccessException(Document):
	def validate(self):
		if self.is_new() or self.has_value_changed("entitlement") or self.has_value_changed("valid_to"):
			self.approved_by = frappe.session.user
