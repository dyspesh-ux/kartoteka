"""Normalisation helpers: person match keys and value comparison for upserts."""

import datetime
import re

from frappe.utils import cint, flt, getdate

# Latin letters that look like Cyrillic ones. Applied after lower-casing.
_LATIN_LOOKALIKES = str.maketrans(
	{
		"a": "а",
		"b": "в",
		"c": "с",
		"e": "е",
		"h": "н",
		"k": "к",
		"m": "м",
		"o": "о",
		"p": "р",
		"t": "т",
		"x": "х",
		"y": "у",
		"ё": "е",
	}
)
_SPACES = re.compile(r"\s+")


def normalize_name(value: str | None) -> str:
	"""Lower case, ё→е, collapsed spaces, Latin look-alikes → Cyrillic."""
	if not value:
		return ""
	value = str(value).lower().translate(_LATIN_LOOKALIKES)
	return _SPACES.sub(" ", value).strip()


def match_keys(last_name, first_name, middle_name, birth_date) -> tuple[str, str]:
	"""Returns (match_key, match_key_partial).

	Without a birth date or a first name the keys are empty: matching people by
	name alone is too risky.
	"""
	birth = iso_date(birth_date)
	first = normalize_name(first_name)
	if not birth or not first:
		return "", ""
	middle = normalize_name(middle_name)
	last = normalize_name(last_name)
	return f"{last}|{first}|{middle}|{birth}", f"{first}|{middle}|{birth}"


def iso_date(value) -> str:
	if not value:
		return ""
	if isinstance(value, datetime.datetime):
		value = value.date()
	if isinstance(value, datetime.date):
		return value.isoformat()
	return getdate(value).isoformat()


def date_or_none(value):
	"""Parses yyyy-MM-dd from the API; treats empty 1C dates (0001-01-01) as None."""
	if not value:
		return None
	date = getdate(value)
	if date.year <= 1:
		return None
	return date


def comparable(value, fieldtype: str):
	"""Canonical representation used to decide whether a field really changed."""
	if fieldtype in ("Check",):
		return cint(value)
	if fieldtype in ("Int",):
		return cint(value)
	if fieldtype in ("Float", "Currency", "Percent"):
		return round(flt(value), 6)
	if fieldtype == "Date":
		return iso_date(value)
	if fieldtype == "Datetime":
		return str(value or "")
	if value is None:
		return ""
	return str(value)
