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
		content = frappe.get_doc("File", {"file_url": self.upload_file}).get_content()
		if isinstance(content, str):
			content = content.encode()
		from access_registry.diadoc.sync import run_upload

		log = run_upload(content, commit=not frappe.flags.in_test)
		return {"status": log.status, "log": log.name, "stats": frappe.parse_json(log.stats or "{}")}
