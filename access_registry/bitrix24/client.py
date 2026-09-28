"""Client of the Bitrix24 REST API (cloud and on-premise) through an incoming webhook.

The client knows the protocol only: calls, batches, paging, limits and errors. What to read and how
to compare it with the registry lives in ``sync.py``.

    client = B24Client("https://b24.example.local/rest/1/abcdef0123456789/")
    users = client.list_all("user.get", {"FILTER": {"ACTIVE": True}})
    client.call("user.update", {"ID": 15, "SECOND_NAME": "Иванович"})

Docs: https://apidocs.bitrix24.com/ (methods, batch, limits).
"""

import json
import time
from urllib.parse import quote, urlsplit

PAGE = 50  # Bitrix24 returns lists by 50 records
BATCH_LIMIT = 50  # commands in one batch
LIMIT_ERRORS = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"}
AUTH_ERRORS = {"NO_AUTH_FOUND", "INVALID_CREDENTIALS", "invalid_token", "expired_token", "WRONG_AUTH_TYPE"}


class B24Error(Exception):
	"""Error answered by Bitrix24 (``code`` is its error code) or a transport failure."""

	def __init__(self, code: str, description: str = "", method: str = ""):
		self.code = code or "ERROR"
		self.description = description or ""
		self.method = method
		super().__init__(f"{method}: {self.code} {self.description}".strip())

	@property
	def is_auth(self) -> bool:
		return self.code in AUTH_ERRORS

	@property
	def is_access(self) -> bool:
		return self.code in {"insufficient_scope", "ACCESS_DENIED", "ERROR_METHOD_NOT_FOUND"} or self.is_auth


def normalize_webhook(url: str) -> str:
	"""https://portal/rest/<user>/<token>/ with exactly one trailing slash; a pasted method is cut off."""
	url = (url or "").strip()
	if not url:
		raise ValueError("Не указан адрес входящего вебхука")
	parts = urlsplit(url)
	if parts.scheme not in ("http", "https") or "/rest/" not in parts.path:
		raise ValueError("Адрес вебхука должен выглядеть как https://портал/rest/<пользователь>/<ключ>/")
	path = parts.path.split("/rest/", 1)[1].strip("/").split("/")
	if len(path) < 2:
		raise ValueError(
			"В адресе вебхука нет пользователя и ключа: https://портал/rest/<пользователь>/<ключ>/"
		)
	return f"{parts.scheme}://{parts.netloc}/rest/{path[0]}/{path[1]}/"


def encode_query(params, prefix="") -> str:
	"""PHP-style query string, as batch commands expect: FILTER[>ID]=5&SELECT[]=ID."""
	items = []
	if isinstance(params, dict):
		pairs = params.items()
	elif isinstance(params, list | tuple):
		pairs = ((str(i), v) for i, v in enumerate(params))
	else:
		return f"{prefix}={quote(_scalar(params), safe='')}"
	for key, value in pairs:
		name = f"{prefix}[{key}]" if prefix else str(key)
		if isinstance(value, dict | list | tuple):
			if not value:
				continue
			items.append(encode_query(value, name))
		else:
			items.append(f"{quote(name, safe='[]')}={quote(_scalar(value), safe='')}")
	return "&".join(i for i in items if i)


def _scalar(value) -> str:
	if value is True:
		return "Y"
	if value is False:
		return "N"
	if value is None:
		return ""
	return str(value)


class B24Client:
	"""Synchronous client. ``session`` is a requests.Session (or a stub with ``post`` in tests)."""

	def __init__(
		self,
		webhook: str,
		timeout: int = 60,
		verify_ssl: bool = True,
		session=None,
		min_interval: float = 0.5,
		retries: int = 5,
		sleep=time.sleep,
	):
		self.base = normalize_webhook(webhook)
		self.timeout = timeout
		self.verify_ssl = verify_ssl
		self.min_interval = min_interval  # Bitrix24: about 2 requests per second per portal
		self.retries = retries
		self.sleep = sleep
		self._last = 0.0
		self.calls = 0
		if session is None:
			import requests

			session = requests.Session()
		self.session = session

	# ------------------------------------------------------------------ transport

	def call(self, method: str, params: dict | None = None) -> dict:
		"""One REST call. Returns the whole answer: ``result`` plus ``next``/``total`` for lists."""
		attempt = 0
		while True:
			attempt += 1
			self._throttle()
			try:
				response = self.session.post(
					self.base + method + ".json",
					data=json.dumps(params or {}, ensure_ascii=False, default=str).encode(),
					headers={"Content-Type": "application/json", "Accept": "application/json"},
					timeout=self.timeout,
					verify=self.verify_ssl,
				)
			except Exception as e:  # network: DNS, TLS, timeout
				if attempt < self.retries:
					self.sleep(min(2**attempt, 30))
					continue
				raise B24Error("NETWORK", str(e), method) from e
			self.calls += 1
			status = getattr(response, "status_code", 200)
			try:
				data = response.json()
			except ValueError:
				data = None
			error = (data or {}).get("error") if isinstance(data, dict) else None
			if (error in LIMIT_ERRORS or status in (429, 503)) and attempt < self.retries:
				self.sleep(min(2**attempt, 30))
				continue
			if error:
				raise B24Error(error, (data or {}).get("error_description", ""), method)
			if status >= 400 or not isinstance(data, dict):
				raise B24Error(f"HTTP_{status}", (getattr(response, "text", "") or "")[:300], method)
			return data

	def _throttle(self):
		wait = self._last + self.min_interval - time.monotonic()
		if wait > 0:
			self.sleep(wait)
		self._last = time.monotonic()

	def result(self, method: str, params: dict | None = None):
		return self.call(method, params).get("result")

	# ------------------------------------------------------------------ batch and lists

	def batch(self, commands: dict, halt: bool = False) -> dict:
		"""Runs up to 50 commands per request: {key: (method, params)} → {key: result}.

		Errors of single commands are raised as B24Error for the first failed key when ``halt``,
		otherwise returned in the result as B24Error instances.
		"""
		results = {}
		items = list(commands.items())
		for start in range(0, len(items), BATCH_LIMIT):
			chunk = items[start : start + BATCH_LIMIT]
			cmd = {
				key: f"{method}?{encode_query(params or {})}".rstrip("?") for key, (method, params) in chunk
			}
			answer = self.result("batch", {"halt": 1 if halt else 0, "cmd": cmd}) or {}
			data = answer.get("result") or {}
			errors = answer.get("result_error") or {}
			totals = answer.get("result_total") or {}
			for key, (method, _params) in chunk:
				if key in errors and errors[key]:
					err = errors[key] if isinstance(errors[key], dict) else {"error": str(errors[key])}
					failure = B24Error(err.get("error", "ERROR"), err.get("error_description", ""), method)
					if halt:
						raise failure
					results[key] = failure
				else:
					results[key] = data.get(key) if isinstance(data, dict) else None
				if key in totals:
					results.setdefault("__total__", {})[key] = totals[key]
		results.pop("__total__", None)
		return results

	def list_all(self, method: str, params: dict | None = None, key: str | None = None) -> list:
		"""All records of a list method. The first page gives the total, the rest go in batches.

		``key`` — for methods that wrap the list (crm.type.list → result.types).
		"""
		params = dict(params or {})
		first = self.call(method, {**params, "start": 0})
		rows = _unwrap(first.get("result"), key)
		total = int(first.get("total") or 0)
		if not first.get("next") or total <= len(rows):
			return rows
		pages = {f"p{start}": (method, {**params, "start": start}) for start in range(PAGE, total, PAGE)}
		answers = self.batch(pages, halt=True)
		for start in range(PAGE, total, PAGE):
			rows.extend(_unwrap(answers.get(f"p{start}"), key))
		return rows

	def call_many(self, method: str, param_list: list) -> list:
		"""The same method for many parameter sets (in batches), results in the same order."""
		answers = self.batch({f"c{i}": (method, p) for i, p in enumerate(param_list)})
		return [answers.get(f"c{i}") for i in range(len(param_list))]

	# ------------------------------------------------------------------ typed helpers

	def users(self) -> list:
		"""All users, active and fired. Portals differ in the default ACTIVE filter, so both are asked."""
		users = {}
		for active in (True, False):
			for row in self.list_all("user.get", {"FILTER": {"ACTIVE": active}, "ADMIN_MODE": True}):
				users[str(row.get("ID"))] = row
		return sorted(users.values(), key=lambda r: int(r.get("ID") or 0))

	def departments(self) -> list:
		return self.list_all("department.get", {"SORT": "ID", "ORDER": "ASC"})

	def workgroups(self) -> list:
		return self.list_all("sonet_group.get", {"ORDER": {"ID": "ASC"}, "IS_ADMIN": "Y"})

	def workgroup_members(self, group_ids: list) -> dict:
		"""{group id: [{USER_ID, ROLE}]}; ROLE: A — владелец, E — модератор, K — участник."""
		answers = self.batch({f"g{gid}": ("sonet_group.user.get", {"ID": gid}) for gid in group_ids})
		result = {}
		for gid in group_ids:
			value = answers.get(f"g{gid}")
			result[str(gid)] = [] if isinstance(value, B24Error) or not value else list(value)
		return result

	def crm_types(self) -> list:
		"""Smart processes (dynamic CRM types)."""
		try:
			return self.list_all("crm.type.list", {}, key="types")
		except B24Error as e:
			if e.is_access:
				return []
			raise

	def update_user(self, user_id, fields: dict):
		return self.result("user.update", {"ID": user_id, **fields})

	def scope(self) -> list:
		return self.result("scope") or []


def _unwrap(value, key=None) -> list:
	if value is None:
		return []
	if key and isinstance(value, dict):
		value = value.get(key) or []
	if isinstance(value, dict):  # some methods return {id: row}
		return list(value.values())
	return list(value)
