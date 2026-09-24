"""HTTP client for the HR_Export_API extension of 1C:ZUP.

Tests replace ``fetch_source_data`` with a function returning fixture payloads.
"""

import frappe
import requests

# Relative paths of the HR_Export_API endpoints.
ENDPOINTS = {
	"meta": "meta",
	"organizations": "organizations",
	"departments": "departments",
	"employees": "employees",
	"absences": "absences",
}


class SourceFetchError(Exception):
	pass


def fetch_source_data(source, date_from, date_to, timeout: int) -> dict:
	"""Downloads everything one sync needs. Returns a dict of payloads.

	``meta`` is diagnostic: a failure there is reported as ``meta_error`` instead of
	stopping the sync.
	"""
	session = _session(source)
	data = {}
	try:
		data["meta"] = _get(session, source, ENDPOINTS["meta"], timeout=timeout)
	except Exception as e:
		data["meta"] = None
		data["meta_error"] = f"{type(e).__name__}: {e}"
	data["organizations"] = _as_list(_get(session, source, ENDPOINTS["organizations"], timeout=timeout))
	data["departments"] = _as_list(_get(session, source, ENDPOINTS["departments"], timeout=timeout))
	data["employees"] = _as_list(_get(session, source, ENDPOINTS["employees"], timeout=timeout))
	data["absences"] = _as_list(
		_get(
			session,
			source,
			ENDPOINTS["absences"],
			params={"from": date_from.isoformat(), "to": date_to.isoformat()},
			timeout=timeout,
		)
	)
	return data


def _session(source) -> requests.Session:
	session = requests.Session()
	password = source.get_password("password", raise_exception=False) if source.password else None
	if source.username:
		session.auth = (source.username, password or "")
	session.verify = bool(source.verify_ssl)
	session.headers["Accept"] = "application/json"
	return session


def _get(session, source, path, params=None, timeout=300):
	url = source.base_url.rstrip("/") + "/" + path
	response = session.get(url, params=params, timeout=timeout)
	if response.status_code != 200:
		raise SourceFetchError(f"GET {url} → HTTP {response.status_code}: {response.text[:500]}")
	response.encoding = "utf-8"
	try:
		return response.json()
	except ValueError as e:
		raise SourceFetchError(f"GET {url}: ответ не JSON ({e}): {response.text[:500]}") from e


def _as_list(payload) -> list:
	"""Endpoints return a JSON array; tolerate a wrapper object with one list inside."""
	if payload is None:
		return []
	if isinstance(payload, list):
		return payload
	if isinstance(payload, dict):
		for key in ("data", "items", "value", "result"):
			if isinstance(payload.get(key), list):
				return payload[key]
		lists = [v for v in payload.values() if isinstance(v, list)]
		if len(lists) == 1:
			return lists[0]
	raise SourceFetchError(f"Неожиданный формат ответа: {frappe.as_json(payload)[:300]}")
