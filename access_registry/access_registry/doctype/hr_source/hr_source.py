# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

SOURCE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


class HRSource(Document):
	def validate(self):
		self.source_code = (self.source_code or "").strip()
		if not SOURCE_CODE_RE.match(self.source_code):
			frappe.throw(
				_("Код источника: 1–20 символов, латиница, цифры, «_» и «-». Он входит в ключи записей и не меняется.")
			)
		if self.base_url:
			self.base_url = self.base_url.strip()
			if not re.match(r"^https?://", self.base_url):
				frappe.throw(_("Базовый URL должен начинаться с http:// или https://"))

	@frappe.whitelist()
	def sync_now(self):
		frappe.only_for("System Manager")
		from access_registry.sync.engine import enqueue_source_sync

		enqueue_source_sync(self.name)
		return _("Синхронизация источника {0} поставлена в очередь long. Результат — в Sync Log.").format(self.name)
