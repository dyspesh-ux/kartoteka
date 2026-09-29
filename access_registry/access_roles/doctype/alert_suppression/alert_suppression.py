# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AlertSuppression(Document):
	"""An entry of the journal of suppressed alerts. Made and closed only by the registry app."""

	def validate(self):
		if not self.flags.from_registry and not frappe.flags.in_import:
			frappe.throw(
				_(
					"Журнал гашений меняется только из приложения реестра: «Контроль» → «Погасить» или «Вернуть»"
				)
			)

	def on_trash(self):
		frappe.throw(
			_("Записи журнала гашений не удаляются. Чтобы замечание снова показывалось, верните его")
		)
