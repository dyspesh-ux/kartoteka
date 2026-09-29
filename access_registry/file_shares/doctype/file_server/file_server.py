# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

from access_registry.permissions import ADMINS, require


class FileServer(Document):
	def validate(self):
		self.server_code = (self.server_code or "").strip()
		if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.server_code):
			frappe.throw(
				_(
					"Код сервера: только латиница, цифры, точка, дефис и подчёркивание — он же SERVER_CODE в сборщике"
				)
			)

	@frappe.whitelist()
	def load_file(self):
		"""Loads the collector output attached to «Файл выгрузки» (for the first run or without network access)."""
		require(*ADMINS)
		if not self.upload_file:
			frappe.throw(
				_("Приложите файл выгрузки сборщика (registry-acl.txt или .gz) в поле «Файл выгрузки»")
			)
		content = frappe.get_doc("File", {"file_url": self.upload_file}).get_content()
		if isinstance(content, str):
			content = content.encode()
		from access_registry.file_shares.sync import run_upload

		log = run_upload(self.name, content, commit=not frappe.flags.in_test)
		return {"status": log.status, "log": log.name}
