# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from access_registry.permissions import ADMINS, require


class RegistryDigest(Document):
	def validate(self):
		if not 0 <= cint(self.send_hour) <= 23:
			frappe.throw(_("Час отправки — от 0 до 23"))
		from access_registry.access_roles.digest import recipients

		chosen = {r.user for r in self.recipients}
		allowed = set(recipients(self))
		emails = dict(
			frappe.get_all(
				"User", filters={"name": ["in", list(chosen) or [""]]}, fields=["name", "email"], as_list=True
			)
		)
		skipped = sorted(u for u in chosen if emails.get(u) not in allowed)
		if skipped:
			frappe.msgprint(
				_("Сводка не уйдёт: {0} — нет доступа к реестру, учётка выключена или нет почты").format(
					", ".join(skipped)
				),
				indicator="gray",
			)

	@frappe.whitelist()
	def preview(self):
		require(*ADMINS)
		from access_registry.access_roles.digest import run

		return run(preview=True).name

	@frappe.whitelist()
	def send_now(self):
		require(*ADMINS)
		from access_registry.access_roles.digest import run

		log = run(force=True)
		return {"status": log.status, "log": log.name, "error": log.error}
