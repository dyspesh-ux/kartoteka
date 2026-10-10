# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
UF_RE = re.compile(r"^UF_[A-Z0-9_]+$")


class B24Portal(Document):
	def validate(self):
		from access_registry.bitrix24.client import normalize_webhook

		self.portal_code = (self.portal_code or "").strip()
		if not CODE_RE.match(self.portal_code):
			frappe.throw(_("Код портала: 1–20 символов, латиница, цифры, «_» и «-»."))
		webhook = self.webhook or ""
		if webhook and not set(webhook) <= {"*"}:
			try:
				self.webhook = normalize_webhook(webhook)
			except ValueError as e:
				frappe.throw(str(e))
			self.portal_url = self.webhook.split("/rest/", 1)[0]
		self.person_uuid_field = (self.person_uuid_field or "").strip().upper()
		if self.person_uuid_field and not UF_RE.match(self.person_uuid_field):
			frappe.throw(_("Поле для UUID — пользовательское поле Битрикс24 вида UF_USR_REGISTRY_ID."))
		if self.max_writes is not None and self.max_writes < 0:
			self.max_writes = 0

	@frappe.whitelist(methods=["POST"])
	def sync_now(self):
		frappe.only_for(("System Manager", "Registry Admin"))
		from frappe.utils.background_jobs import is_job_enqueued

		from access_registry.bitrix24.sync import enqueue

		if is_job_enqueued(f"bitrix24_sync::{self.name}"):
			return _("Загрузка портала {0} уже в очереди или выполняется.").format(self.name)
		enqueue(self.name)
		return _("Загрузка портала {0} поставлена в очередь. Результат — в журнале синхронизаций.").format(
			self.name
		)

	@frappe.whitelist(methods=["POST"])
	def test_connection(self):
		frappe.only_for(("System Manager", "Registry Admin"))
		from access_registry.bitrix24.sync import fetch_export, make_client

		lines = []
		try:
			client = make_client(self)
			scope = client.scope()
			users = client.call("user.get", {"start": 0})
			lines.append(
				_("REST работает: пользователей {0}, права вебхука: {1}.").format(
					users.get("total", "?"), ", ".join(scope) or "—"
				)
			)
			missing = [s for s in ("user", "department", "sonet_group", "crm") if s not in scope]
			if missing:
				lines.append(_("Не хватает прав вебхука: {0}.").format(", ".join(missing)))
		except Exception as e:
			lines.append(_("REST не работает: {0}").format(frappe.utils.escape_html(str(e))))
		if not self.exporter_url:
			lines.append(
				_(
					"Скрипт выгрузки не указан: права на CRM, смарт-процессы, общие диски, группы пользователей "
					"и график отсутствий загружаться не будут (REST их не отдаёт)."
				)
			)
		else:
			try:
				data = fetch_export(self)
				lines.append(
					_(
						"Скрипт выгрузки работает: ролей CRM {0}, назначений ролей {1}, прав на папки {2}, "
						"групп пользователей {3}, отсутствий {4}."
					).format(
						len(data.get("crm_roles") or []),
						len(data.get("crm_role_relations") or []),
						len(data.get("disk_rights") or []),
						len(data.get("user_groups") or []),
						len(data.get("absences") or []),
					)
				)
				modules = (data.get("diagnostics") or {}).get("modules") or {}
				if modules:
					lines.append(
						_("Модули: {0}").format(
							frappe.utils.escape_html(", ".join(f"{k} {v}" for k, v in modules.items()))
						)
					)
				for warning in data.get("warnings") or []:
					lines.append(_("Скрипт сообщает: {0}").format(frappe.utils.escape_html(str(warning))))
				if int(data.get("version") or 0) < 2:
					lines.append(
						_(
							"На портале старая версия скрипта: замените её на bitrix24/registry_export.php из репозитория."
						)
					)
			except Exception as e:
				lines.append(_("Скрипт выгрузки не работает: {0}").format(frappe.utils.escape_html(str(e))))
		return "<br>".join(lines)
