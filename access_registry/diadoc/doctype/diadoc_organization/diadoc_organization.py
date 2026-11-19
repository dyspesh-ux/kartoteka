# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from access_registry.permissions import ADMINS, DD_SPECIALIST, require


class DiadocOrganization(Document):
	"""A Diadoc subscriber organization. Records arrive by manual xlsx uploads."""

	@frappe.whitelist()
	def load_file(self):
		"""Loads the registry export attached to «Файл выгрузки» («Загрузить выгрузку» button)."""
		require(*ADMINS, DD_SPECIALIST)
		if not self.upload_file:
			frappe.throw(_("Приложите файл выгрузки (xlsx) в поле «Файл выгрузки»"))
		attached = frappe.get_doc("File", {"file_url": self.upload_file})
		content = attached.get_content()
		if isinstance(content, str):
			content = content.encode()
		return self._run(content, file_name=attached.file_name)

	@staticmethod
	def _run(content: bytes, file_name: str | None = None) -> dict:
		from access_registry.diadoc.sync import run_upload

		log = run_upload(content, commit=not frappe.flags.in_test, file_name=file_name)
		return {"status": log.status, "log": log.name, "stats": frappe.parse_json(log.stats or "{}")}
