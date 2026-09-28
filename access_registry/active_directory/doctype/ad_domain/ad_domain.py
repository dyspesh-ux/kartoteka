# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


class ADDomain(Document):
	def validate(self):
		self.domain_code = (self.domain_code or "").strip()
		if not CODE_RE.match(self.domain_code):
			frappe.throw(_("Код домена: 1–20 символов, латиница, цифры, «_» и «-»."))
		self.netbios_name = (self.netbios_name or "").strip().upper()
		for url in (self.ldap_url or "").split():
			if not re.match(r"^ldaps?://", url, re.I):
				frappe.throw(_("Сервер LDAP должен начинаться с ldap:// или ldaps://"))

	@frappe.whitelist()
	def sync_now(self):
		frappe.only_for("System Manager")
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.active_directory.sync import enqueue, job_id_for

		if is_job_enqueued(job_id_for(self.name)):
			return _("Загрузка домена {0} уже в очереди или выполняется.").format(self.name)
		enqueue(self.name)
		return _("Загрузка домена {0} поставлена в очередь. Результат — в журнале синхронизаций.").format(
			self.name
		)

	@frappe.whitelist()
	def test_connection(self):
		frappe.only_for("System Manager")
		from access_registry.active_directory.ldap_client import test_connection

		return test_connection(self)
