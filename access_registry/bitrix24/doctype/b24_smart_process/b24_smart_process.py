# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
FIELDS = ("requester_field", "category_field", "source_field", "deadline_field", "done_field")


class B24SmartProcess(Document):
	def autoname(self):
		self.name = f"{self.portal}:{int(self.entity_type_id or 0)}"

	def validate(self):
		if not self.entity_type_id or self.entity_type_id <= 0:
			frappe.throw(_("Укажите ID смарт-процесса (entityTypeId) из файла структуры, например 1210."))
		for field in FIELDS:
			value = (self.get(field) or "").strip()
			if value and not FIELD_RE.match(value):
				frappe.throw(
					_("{0}: код поля Битрикс24 вида ufCrm50_1748612217.").format(self.meta.get_label(field))
				)
			self.set(field, value)
		if self.history_days is not None and self.history_days < 1:
			self.history_days = 365

	@frappe.whitelist(methods=["POST"])
	def sync_now(self):
		frappe.only_for(("System Manager", "Registry Admin"))
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.bitrix24.smart_items import enqueue

		if is_job_enqueued(f"b24_smart_sync::{self.name}"):
			return _("Загрузка уже в очереди или выполняется.")
		enqueue(self.name)
		return _("Загрузка заявок поставлена в очередь. Результат — в журнале синхронизаций.")
