"""Values from the sources that do not fit their field are shortened instead of failing the load.

Snipe-IT, Bitrix24, AD and 1C have no limit on names and titles («Монитор Xiaomi 2K A27Qi 2026
[120 Hz, 2560x1440, IPS, …]»), while a Data field holds 140 characters. A field the registry
fills itself (read only in the form) is cut to its length with «…»; fields people type in are left
to the usual check.
"""

import frappe

VARCHAR = {"Data"}
DEFAULT_LENGTH = 140


def _app_modules() -> set:
	if not hasattr(frappe.local, "access_registry_modules"):
		frappe.local.access_registry_modules = set(frappe.get_module_list("access_registry"))
	return frappe.local.access_registry_modules


def fit(text, length: int = DEFAULT_LENGTH):
	if not isinstance(text, str) or len(text) <= length:
		return text
	return text[: length - 1].rstrip() + "…"


def fit_lengths(doc, method=None):
	"""doc_events «*» → before_validate: only DocTypes of this app."""
	meta = frappe.get_meta(doc.doctype)
	if meta.module not in _app_modules():
		return
	for d in [doc, *doc.get_all_children()]:
		child_meta = meta if d is doc else frappe.get_meta(d.doctype)
		for df in child_meta.fields:
			if df.fieldtype in VARCHAR and df.read_only:
				value = d.get(df.fieldname)
				length = df.length or DEFAULT_LENGTH
				if isinstance(value, str) and len(value) > length:
					d.set(df.fieldname, fit(value, length))
