# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today


class AccessException(Document):
	def validate(self):
		if self.is_new() or self.has_value_changed("entitlement") or self.has_value_changed("valid_to"):
			from access_registry.access_roles.review import persons_of_user

			if self.person in persons_of_user():
				raise frappe.PermissionError(
					_("Исключение для себя согласовать нельзя: это решает другой человек")
				)
			if self.valid_to and getdate(self.valid_to) < getdate(today()):
				frappe.throw(_("Срок исключения уже прошёл"))
			self.approved_by = frappe.session.user
