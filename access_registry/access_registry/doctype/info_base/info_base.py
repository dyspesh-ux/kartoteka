# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

SOURCE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
HR_CONFIGURATION = "ЗУП"


def is_hr_source(configuration: str | None) -> bool:
	"""Only 1C:ZUP bases are the source of truth about employees, departments and organizations."""
	return configuration == HR_CONFIGURATION


class InfoBase(Document):
	def validate(self):
		self.source_code = (self.source_code or "").strip()
		if not SOURCE_CODE_RE.match(self.source_code):
			frappe.throw(
				_(
					"Код базы: 1–20 символов, латиница, цифры, «_» и «-». Он входит в ключи записей и не меняется."
				)
			)
		for field, label in (("base_url", "HR_Export_API"), ("itaccess_url", "ITAccess")):
			value = (self.get(field) or "").strip()
			self.set(field, value)
			if value and not re.match(r"^https?://", value):
				frappe.throw(_("URL сервиса {0} должен начинаться с http:// или https://").format(label))
		if not is_hr_source(self.configuration) and not self.is_new():
			if frappe.db.exists("Employment", {"source": self.name}):
				frappe.throw(
					_("По базе {0} уже загружены кадровые данные: конфигурацию ЗУП сменить нельзя.").format(
						self.name
					)
				)

	@frappe.whitelist()
	def sync_now(self):
		frappe.only_for(("System Manager", "Registry Admin"))
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.sync.engine import enqueue_source_sync, job_id_for

		if not is_hr_source(self.configuration):
			frappe.throw(_("Кадровые данные загружаются только из баз ЗУП"))
		if is_job_enqueued(job_id_for(self.name)):
			return _("Синхронизация кадров {0} уже в очереди или выполняется.").format(self.name)
		enqueue_source_sync(self.name)
		return _(
			"Синхронизация кадров {0} поставлена в очередь. Результат — в журнале синхронизаций."
		).format(self.name)

	@frappe.whitelist()
	def load_rights_now(self):
		return self._enqueue_catalog("snapshot", _("Загрузка пользователей и прав"))

	@frappe.whitelist()
	def load_log_now(self):
		return self._enqueue_catalog("log", _("Загрузка журнала изменений прав"))

	def _enqueue_catalog(self, kind, title):
		frappe.only_for(("System Manager", "Registry Admin"))
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.access_catalog.pull import enqueue, job_id_for

		if not self.itaccess_enabled:
			frappe.throw(_("Загрузка пользователей и прав для базы {0} выключена").format(self.name))
		if is_job_enqueued(job_id_for(self.name, kind)):
			return _("{0} для {1} уже в очереди или выполняется.").format(title, self.name)
		enqueue(self.name, kind)
		return _("{0} для {1} поставлена в очередь. Результат — в журнале синхронизаций.").format(
			title, self.name
		)
