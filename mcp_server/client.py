"""Minimal read-only Frappe REST client for the Kartoteka MCP server.

Calls the registry's whitelisted API methods over GET with token auth. The registry
checks every permission itself; this client only carries the credentials of a dedicated
read-only user and adds nothing on top.
"""

import json
import os
from urllib.parse import urlencode

import requests

ENV_URL = "KARTOTEKA_URL"
ENV_API_KEY = "KARTOTEKA_API_KEY"
ENV_API_SECRET = "KARTOTEKA_API_SECRET"
ENV_TIMEOUT = "KARTOTEKA_TIMEOUT"
ENV_VERIFY_SSL = "KARTOTEKA_VERIFY_SSL"


class RegistryError(RuntimeError):
	"""A registry call failed; the message carries the server-side reason."""


def _encode(value):
	return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _error_text(payload: dict) -> str:
	"""Human-readable reason from a Frappe error body (messages may be HTML-wrapped)."""
	for key in ("exception", "exc_type", "message"):
		text = payload.get(key)
		if isinstance(text, str) and text:
			return text.splitlines()[0].removeprefix("frappe.exceptions.").removeprefix("Exception: ")
	raw = payload.get("_server_messages")
	if isinstance(raw, str):
		try:
			messages = json.loads(raw)
		except ValueError:
			messages = []
		for item in messages:
			if isinstance(item, dict) and item.get("message"):
				return str(item["message"]).splitlines()[0]
	return ""


class FrappeClient:
	"""GET-only client: no method here can change anything in the registry."""

	def __init__(self, url: str, api_key: str, api_secret: str, timeout: float = 30.0, verify: bool = True):
		self.base_url = url.rstrip("/")
		self.timeout = timeout
		self.session = requests.Session()
		self.session.headers["Authorization"] = f"token {api_key}:{api_secret}"
		self.session.verify = verify

	@classmethod
	def from_env(cls) -> "FrappeClient":
		try:
			url = os.environ[ENV_URL]
			api_key = os.environ[ENV_API_KEY]
			api_secret = os.environ[ENV_API_SECRET]
		except KeyError as e:
			raise RegistryError(
				f"Set {ENV_URL}, {ENV_API_KEY} and {ENV_API_SECRET} in the environment (see mcp_server/README.md)"
			) from e
		verify = os.environ.get(ENV_VERIFY_SSL, "1").strip().lower() not in ("0", "false", "no")
		return cls(url, api_key, api_secret, timeout=float(os.environ.get(ENV_TIMEOUT, 30)), verify=verify)

	def call(self, method: str, **params):
		"""GET /api/method/<method> with the given params; returns the decoded «message»."""
		query = {k: _encode(v) for k, v in params.items() if v is not None}
		url = f"{self.base_url}/api/method/{method}"
		if query:
			url = f"{url}?{urlencode(query)}"
		try:
			response = self.session.get(url, timeout=self.timeout)
		except requests.RequestException as e:
			raise RegistryError(f"Не удалось связаться с реестром: {e}") from e
		try:
			payload = response.json()
		except ValueError:
			raise RegistryError(f"Реестр вернул ответ без JSON (HTTP {response.status_code})") from None
		if response.ok and "message" in payload:
			return payload["message"]
		reason = _error_text(payload) if isinstance(payload, dict) else ""
		raise RegistryError(reason or f"HTTP {response.status_code}")
