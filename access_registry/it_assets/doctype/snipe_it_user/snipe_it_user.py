# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe.model.document import Document

MANUAL = "Вручную"


class SnipeITUser(Document):
	def validate(self):
		# A manual link always wins; the sync never changes manual_person.
		if self.manual_person:
			self.person = self.manual_person
			self.person_link_method = MANUAL
			self.person_link_note = ""
		elif self.person_link_method == MANUAL:
			self.person = None
			self.person_link_method = ""
			self.person_link_note = "Ручная привязка снята, сотрудник найдётся при следующей загрузке"
