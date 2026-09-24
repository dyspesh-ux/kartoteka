# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from access_registry.sync.persons import merge_persons, pair_key


class PersonMergeCandidate(Document):
	def validate(self):
		if self.is_new():
			self.pair_key = pair_key(self.person_a, self.person_b)

	@frappe.whitelist()
	def merge(self):
		frappe.only_for("System Manager")
		if self.status != "Открыт":
			frappe.throw(_("Кандидат уже разобран: {0}").format(self.status))
		if not (self.person_a and self.person_b):
			frappe.throw(_("Не указаны оба человека"))
		merge_persons(self.person_a, self.person_b, candidate=self.name)
		return _("Человек {0} влит в {1}").format(self.person_b, self.person_a)

	@frappe.whitelist()
	def mark_different(self):
		frappe.only_for("System Manager")
		if self.status != "Открыт":
			frappe.throw(_("Кандидат уже разобран: {0}").format(self.status))
		self.status = "Разные люди"
		self.save()
		return _("Пара помечена как разные люди и больше не будет предложена")
