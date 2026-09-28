# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AccessRole(Document):
	def validate(self):
		self.role_name = (self.role_name or "").strip()
		seen = set()
		rows = []
		for row in self.entitlements:
			if row.entitlement in seen:
				continue
			seen.add(row.entitlement)
			rows.append(row)
		self.entitlements = rows
		for idx, row in enumerate(self.entitlements, start=1):
			row.idx = idx
		if self.kind != "Должностная":
			self.rules = []
		elif self.status == "Действует" and not self.rules:
			frappe.throw(_("У должностной роли нужно хотя бы одно правило: кому она положена."))
		for rule in self.rules:
			rule.position_title = (rule.position_title or "").strip()
			if not (rule.position_title or rule.department or rule.organization):
				frappe.throw(
					_("Правило {0}: укажите должность, подразделение или организацию.").format(rule.idx)
				)
