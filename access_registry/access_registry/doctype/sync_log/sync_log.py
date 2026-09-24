# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now


class SyncLog(Document):
	@staticmethod
	def clear_old_logs(days=180):
		"""Called by Log Settings; the retention period is set there (default in hooks.py)."""
		log = frappe.qb.DocType("Sync Log")
		old = (
			frappe.qb.from_(log)
			.select(log.name)
			.where(log.creation < (Now() - Interval(days=days)))
			.run(pluck=True)
		)
		if not old:
			return
		event = frappe.qb.DocType("HR Event")
		frappe.qb.update(event).set(event.sync_log, None).where(event.sync_log.isin(old)).run()
		frappe.db.delete(log, filters=log.name.isin(old))
