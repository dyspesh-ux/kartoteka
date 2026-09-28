# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from access_registry.permissions import ROLE_MANAGER, require


class AccessReview(Document):
	def validate(self):
		if not self.is_new() and self.status != "Черновик":
			for field in ("system", "organization", "only_privileged", "include_uncatalogued", "reviewer_mode"):
				if self.has_value_changed(field):
					frappe.throw(_("Пересмотр уже начат: состав и режим проверки менять нельзя"))

	def on_trash(self):
		frappe.db.delete("Access Review Item", {"access_review": self.name})

	@frappe.whitelist()
	def start(self):
		require(ROLE_MANAGER)
		from access_registry.access_roles.review import start_review

		counts = start_review(self.name)
		return _("Пересмотр начат: доступов на проверке {0}, без проверяющего {1}.").format(
			counts["items_total"], counts["items_unassigned"]
		)

	@frappe.whitelist()
	def finish(self):
		require(ROLE_MANAGER)
		from access_registry.access_roles.review import finish_review

		counts = finish_review(self.name)
		return _("Пересмотр завершён: решено {0} из {1}, на отзыв {2}.").format(
			counts["items_done"], counts["items_total"], counts["items_revoke"]
		)
