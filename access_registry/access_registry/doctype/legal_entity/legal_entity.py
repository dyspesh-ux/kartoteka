# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe.model.document import Document


class LegalEntity(Document):
	def autoname(self):
		self.inn = (self.inn or "").strip()
		self.kpp = (self.kpp or "").strip()
		self.name = f"{self.inn}-{self.kpp}" if self.kpp else self.inn
