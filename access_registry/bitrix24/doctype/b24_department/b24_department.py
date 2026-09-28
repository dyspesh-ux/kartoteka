# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from frappe.model.document import Document

MANUAL = "Вручную"


class B24Department(Document):
	def validate(self):
		if self.manual_hr_department:
			self.hr_department = self.manual_hr_department
			self.hr_link_method = MANUAL
			self.hr_link_note = ""
		elif self.hr_link_method == MANUAL:
			self.hr_department = None
			self.hr_link_method = ""
			self.hr_link_note = "Ручное сопоставление снято, пересчитается при следующей загрузке"
