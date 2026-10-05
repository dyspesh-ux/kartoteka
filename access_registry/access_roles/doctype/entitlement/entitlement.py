# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

FIELDS = {
	"1С": {"ib_profile", "bit_kind", "bit_name", "bit_base"},
	"Active Directory": {"ad_group"},
	"Битрикс24": {"b24_workgroup", "b24_portal", "b24_via", "b24_resource"},
	"Другое": {"other_reference"},
}
ALL_FIELDS = set().union(*FIELDS.values())


class Entitlement(Document):
	def validate(self):
		self.title = (self.title or "").strip()
		for field in ALL_FIELDS - FIELDS.get(self.system, set()):
			self.set(field, None)
		if self.ib_profile and self.bit_kind:
			frappe.throw(_("Укажите либо профиль 1С, либо право БИТ.Финанс, не оба сразу."))
		if self.bit_kind and not (self.bit_name or "").strip():
			frappe.throw(_("Укажите название визы, роли или ЦФО БИТ.Финанс."))
		self.bit_name = (self.bit_name or "").strip() or None
		if self.b24_workgroup and self.b24_via:
			frappe.throw(_("Укажите либо группу Битрикс24, либо «через», не оба сразу."))
		self.check_duplicate()

	def check_duplicate(self):
		for field in ("ib_profile", "ad_group", "b24_workgroup"):
			value = self.get(field)
			if value:
				other = frappe.db.get_value("Entitlement", {field: value, "name": ["!=", self.name]}, "name")
				if other:
					frappe.throw(_("Это право уже есть в каталоге: {0}").format(other))
		if self.bit_kind:
			other = frappe.db.get_value(
				"Entitlement",
				{
					"bit_kind": self.bit_kind,
					"bit_name": self.bit_name,
					"bit_base": self.bit_base or ("is", "not set"),
					"name": ["!=", self.name],
				},
				"name",
			)
			if other:
				frappe.throw(_("Это право уже есть в каталоге: {0}").format(other))
		if self.b24_via:
			other = frappe.db.get_value(
				"Entitlement",
				{
					"b24_via": self.b24_via,
					"b24_resource": self.b24_resource or ("is", "not set"),
					"b24_portal": self.b24_portal or ("is", "not set"),
					"name": ["!=", self.name],
				},
				"name",
			)
			if other:
				frappe.throw(_("Это право уже есть в каталоге: {0}").format(other))
