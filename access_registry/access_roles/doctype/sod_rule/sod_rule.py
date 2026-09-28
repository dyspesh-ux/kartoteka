# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SoDRule(Document):
	def validate(self):
		a = {row.entitlement for row in self.side_a}
		b = {row.entitlement for row in self.side_b}
		if not a or not b:
			frappe.throw(_("Заполните обе стороны конфликта."))
		if a & b:
			frappe.throw(_("Одно и то же право не может быть на обеих сторонах конфликта."))
