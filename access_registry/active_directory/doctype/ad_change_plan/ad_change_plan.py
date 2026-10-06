# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

DRAFT = "Черновик"
DECIDED = ("Одобрен", "Отклонён")


class ADChangePlan(Document):
	"""The four-eyes rule holds for the desk and the REST API too, not only for the app:
	- a decision is taken only through the app's decide_ad_plan (``flags.deciding``), never by the
	  author of the plan;
	- a decided plan does not change any more (the script must run what ИБ approved)."""

	def validate(self):
		if self.is_new():
			if self.status != DRAFT or self.approved_by:
				frappe.throw(_("Новый план — всегда черновик"))
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if before.status in DECIDED:
			frappe.throw(
				_("Решение по плану {0} принято ({1}): менять его нельзя").format(self.name, before.status)
			)
		if self.status != before.status:
			if not self.flags.deciding:
				frappe.throw(_("Одобрить или отклонить план можно только в приложении реестра"))
			if self.approved_by == self.owner:
				frappe.throw(_("Свой план одобрить нельзя: нужен второй человек"))
		elif self.approved_by != before.approved_by or self.decided_on != before.decided_on:
			frappe.throw(_("Поля решения заполняются только при решении по плану"))
		self._items_unchanged_except_include(before)

	def _items_unchanged_except_include(self, before):
		"""In a draft only the checkbox «В плане» may change: the rows themselves come from the registry."""
		fields = (
			"action",
			"account",
			"attribute",
			"before",
			"after",
			"object_guid",
			"distinguished_name",
			"sam_account_name",
		)
		old = {i.name: tuple(i.get(f) for f in fields) for i in before.items}
		new = {i.name: tuple(i.get(f) for f in fields) for i in self.items}
		if old != new:
			frappe.throw(_("Строки плана собирает реестр; в черновике можно только снять или вернуть строку"))
