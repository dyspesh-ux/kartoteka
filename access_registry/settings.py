"""Access Registry Settings with code-level defaults.

The Single may never have been saved, so every value falls back to a default here.
"""

import frappe
from frappe.utils import cint

DEFAULT_SYNC_USERS = ("sync-zup@access.local", "sync-ad@access.local")

DEFAULTS = {
	"shrink_threshold_pct": 10,
	"guard_min_records": 20,
	"sync_user": "sync-zup@access.local",
	"head_keywords": "руководитель\nначальник\nдиректор\nзаведующий\nглавный\nзаместитель",
	"absence_days_back": 30,
	"absence_days_ahead": 180,
	"http_timeout": 300,
	"job_timeout": 7200,
}

INT_FIELDS = {
	"shrink_threshold_pct",
	"guard_min_records",
	"absence_days_back",
	"absence_days_ahead",
	"http_timeout",
	"job_timeout",
}


def get_settings() -> frappe._dict:
	values = frappe._dict(DEFAULTS)
	stored = {}
	try:
		stored = frappe.db.get_singles_dict("Access Registry Settings") or {}
	except Exception:
		stored = {}
	for key in DEFAULTS:
		value = stored.get(key)
		if value is None or value == "":
			continue
		values[key] = cint(value) if key in INT_FIELDS else value
	values.head_keywords_list = parse_keywords(values.head_keywords)
	return values


def parse_keywords(text: str | None) -> list[str]:
	result = []
	for chunk in (text or "").replace(",", "\n").replace(";", "\n").splitlines():
		chunk = chunk.strip()
		if chunk:
			result.append(chunk)
	return result
