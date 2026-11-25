# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from access_registry.permissions import require

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


class ZKBioServer(Document):
	def validate(self):
		self.server_code = (self.server_code or "").strip()
		if not CODE_RE.match(self.server_code):
			frappe.throw(_("Код сервера: 1–20 символов, латиница, цифры, «_» и «-»."))

	@frappe.whitelist()
	def test_connection(self):
		require("Registry Admin")
		from access_registry.zkbio import pg_client

		info = pg_client.test_connection(self)
		return _("Подключение успешно: {0}, персонала {1}, событий {2}").format(
			info["version"], info["persons"], info["events"]
		)

	@frappe.whitelist()
	def refetch_events(self, days: int):
		require("Registry Admin")
		from frappe.utils import add_days, now_datetime

		days = min(max(cint(days), 1), 180)
		self.db_set("events_watermark", add_days(now_datetime(), -days))
		return _(
			"Окно событий сдвинуто на {0} дн. назад; ближайший синк перечитает события (дубликаты пропускаются)."
		).format(days)

	@frappe.whitelist()
	def sync_now(self):
		require("Registry Admin")
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.zkbio.sync import enqueue, job_id_for

		for phase in ("persons", "events"):
			if is_job_enqueued(job_id_for(self.name, phase)):
				return _("Загрузка сервера {0} уже в очереди или выполняется.").format(self.name)
		enqueue(self.name, "persons")  # first: events of the next phase link through personnel
		enqueue(self.name, "events")
		return _("Загрузка сервера {0} поставлена в очередь. Результат — в журнале синхронизаций.").format(
			self.name
		)
