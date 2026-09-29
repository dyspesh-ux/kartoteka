"""Business processes: Excel template and export."""

import frappe
from frappe.utils import today

from access_registry.permissions import READERS, require


@frappe.whitelist()
def download_template():
	"""The workbook with current processes, roles and participants (examples when there are none)."""
	require(*READERS)
	from access_registry.business_processes.xlsx_io import build_workbook

	frappe.response["filename"] = f"business-processes-{today()}.xlsx"
	frappe.response["filecontent"] = build_workbook()
	frappe.response["type"] = "binary"
