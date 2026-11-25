# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ZKBioPerson(Document):
	def validate(self):
		# A manual link always wins; the sync never touches it.
		if self.manual_person:
			self.person = self.manual_person
			self.person_link_method = "Вручную"
			self.person_link_note = ""
