# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe import _
from frappe.model.document import Document

from access_registry.sync.normalize import match_keys


class Person(Document):
	def autoname(self):
		# The person's own UUID: it goes to AD employeeNumber and never changes.
		if not self.person_uuid:
			self.person_uuid = str(uuid.uuid4())
		self.name = self.person_uuid

	def validate(self):
		if not self.is_new() and self.person_uuid != self.name:
			frappe.throw(_("UUID человека не меняется"))
		self.full_name = " ".join(filter(None, [self.last_name, self.first_name, self.middle_name]))
		self.match_key, self.match_key_partial = match_keys(
			self.last_name, self.first_name, self.middle_name, self.birth_date
		)
