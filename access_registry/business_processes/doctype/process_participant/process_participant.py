# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class ProcessParticipant(Document):
	def validate(self):
		if self.valid_from and self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("«По» раньше, чем «С»."))
		other = frappe.db.get_value(
			"Process Participant",
			{"process_role": self.process_role, "person": self.person, "name": ["!=", self.name]},
			"name",
		)
		if other:
			frappe.throw(_("Этот сотрудник уже участник этой роли: {0}").format(other))
