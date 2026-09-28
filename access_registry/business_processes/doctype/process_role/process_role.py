# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ProcessRole(Document):
	def validate(self):
		self.role_name = (self.role_name or "").strip()
		other = frappe.db.get_value(
			"Process Role",
			{
				"business_process": self.business_process,
				"role_name": self.role_name,
				"name": ["!=", self.name],
			},
			"name",
		)
		if other:
			frappe.throw(_("В процессе уже есть роль «{0}».").format(self.role_name))
		seen = set()
		self.entitlements = [
			r for r in self.entitlements if not (r.entitlement in seen or seen.add(r.entitlement))
		]
