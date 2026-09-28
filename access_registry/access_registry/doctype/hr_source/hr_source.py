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
				_(
					"Код источника: 1–20 символов, латиница, цифры, «_» и «-». Он входит в ключи записей и не меняется."
				)
			)
		if self.base_url:
			self.base_url = self.base_url.strip()
			if not re.match(r"^https?://", self.base_url):
				frappe.throw(_("Базовый URL должен начинаться с http:// или https://"))

	@frappe.whitelist()
	def sync_now(self):
		frappe.only_for("System Manager")
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.sync.engine import enqueue_source_sync, job_id_for

		if is_job_enqueued(job_id_for(self.name)):
			return _("Синхронизация источника {0} уже в очереди или выполняется.").format(self.name)
		enqueue_source_sync(self.name)
		return _("Синхронизация источника {0} поставлена в очередь long. Результат — в Sync Log.").format(
			self.name
		)

	@frappe.whitelist()
	def load_rights_now(self):
		return self._enqueue_catalog("snapshot", _("Загрузка прав пользователей 1С"))

	@frappe.whitelist()
	def load_log_now(self):
		return self._enqueue_catalog("log", _("Загрузка журнала 1С"))

	def _enqueue_catalog(self, kind, title):
		frappe.only_for("System Manager")
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.access_catalog.pull import enqueue, job_id_for

		if not self.itaccess_enabled:
			frappe.throw(_("Загрузка прав 1С для источника {0} выключена").format(self.name))
		if is_job_enqueued(job_id_for(self.name, kind)):
			return _("{0} для {1} уже в очереди или выполняется.").format(title, self.name)
		enqueue(self.name, kind)
		return _("{0} для {1} поставлена в очередь long. Результат — в Sync Log.").format(title, self.name)
