# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class ProcessImport(Document):
	def validate(self):
		if self.import_file and not self.import_file.lower().endswith((".xlsx", ".xlsm")):
			frappe.throw(_("Нужен файл Excel .xlsx"))
		if self.has_value_changed("import_file") and self.status != "Загружен":
			self.status = "Новый"

	def content(self) -> bytes:
		file = frappe.get_doc("File", {"file_url": self.import_file})
		return file.get_content()

	def execute(self, apply: bool) -> dict:
		from access_registry.business_processes.xlsx_io import ProcessImport as Importer

		if self.status == "Загружен":
			frappe.throw(_("Этот файл уже загружен. Для новой загрузки создайте новый импорт."))
		result = Importer(self.content(), bool(self.remove_missing_participants)).run(apply=apply)
		counters = ", ".join(f"{k}: {v}" for k, v in result["counters"].items()) or _("изменений нет")
		if result["errors"]:
			self.status = "Есть ошибки"
			self.summary = _("Ошибок: {0}. Ничего не загружено.").format(len(result["errors"]))
		elif result["applied"]:
			self.status = "Загружен"
			self.summary = counters
			self.imported_on = now_datetime()
		else:
			self.status = "Проверен"
			self.summary = _("Ошибок нет. Будет: {0}").format(counters)
		self.errors = "\n".join(result["errors"])
		self.save()
		return result

	@frappe.whitelist()
	def check(self):
		return self.execute(apply=False)

	@frappe.whitelist()
	def load(self):
		return self.execute(apply=True)
