"""Read-only client of the Snipe-IT REST API (/api/v1): hardware, users and the activity log.

Only GET requests. The API key belongs to a Snipe-IT user with view rights; it is stored in the
Password field of «Snipe-IT Server».
"""

import time
from datetime import datetime

import requests

PAGE = 500  # Snipe-IT's default max_results


class SnipeITError(Exception):
	def __init__(self, message: str, status: int | None = None):
		super().__init__(message)
		self.status = status


HINTS = {
	401: "ключ API не принят: проверьте «Ключ API» в карточке сервера (Account → Manage API Keys в Snipe-IT)",
	403: "у пользователя ключа нет прав на это (для техники — «Активы: просмотр», для пользователей — "
	"«Пользователи: просмотр», для журнала выдач — «Отчёты: просмотр»)",
	404: "по этому адресу нет API Snipe-IT: укажите адрес без /api/v1, например https://snipeit.example.local",
	429: "Snipe-IT ограничил частоту запросов (API_THROTTLE_PER_MINUTE): повторите позже или поднимите лимит",
}


class SnipeITClient:
	def __init__(self, base_url: str, token: str, timeout: int = 60, verify_ssl: bool = True, session=None):
		self.base = (base_url or "").rstrip("/")
		if self.base.endswith("/api/v1"):
			self.base = self.base[: -len("/api/v1")]
		self.timeout = timeout
		self.verify = verify_ssl
		self.session = session or requests.Session()
		self.session.headers.update(
			{
				"Authorization": f"Bearer {token}",
				"Accept": "application/json",
				"Content-Type": "application/json",
			}
		)

	def get(self, path: str, params: dict | None = None) -> dict:
		url = f"{self.base}/api/v1/{path.lstrip('/')}"
		for attempt in range(3):
			response = self.session.get(url, params=params or {}, timeout=self.timeout, verify=self.verify)
			if response.status_code == 429 and attempt < 2:
				time.sleep(5 * (attempt + 1))
				continue
			break
		if response.status_code != 200:
			hint = HINTS.get(response.status_code, "")
			raise SnipeITError(
				f"Snipe-IT ответил HTTP {response.status_code} на {path}{': ' + hint if hint else ''}",
				status=response.status_code,
			)
		try:
			data = response.json()
		except ValueError as e:
			raise SnipeITError(
				f"Snipe-IT вернул не JSON на {path} (часто это страница входа: адрес или ключ неверны): "
				f"{response.text[:200]}"
			) from e
		if isinstance(data, dict) and data.get("status") == "error":
			raise SnipeITError(f"Snipe-IT: {data.get('messages')}")
		return data

	def rows(self, path: str, params: dict | None = None, stop=None) -> list[dict]:
		"""All rows of a paged list; ``stop(row)`` ends the reading early (older activity)."""
		result, offset = [], 0
		while True:
			data = self.get(path, {**(params or {}), "limit": PAGE, "offset": offset})
			rows = data.get("rows") or []
			for row in rows:
				if stop and stop(row):
					return result
				result.append(row)
			offset += len(rows)
			if not rows or offset >= int(data.get("total") or 0):
				return result

	def hardware(self) -> list[dict]:
		return self.rows("hardware", {"sort": "id", "order": "asc"})

	def users(self) -> list[dict]:
		return self.rows("users", {"sort": "id", "order": "asc"})

	def activity(self, since: datetime) -> list[dict]:
		"""The activity log, newest first, up to ``since``."""

		def older(row):
			created = parse_datetime((row.get("created_at") or {}).get("datetime"))
			return created is not None and created < since

		return self.rows("reports/activity", {"sort": "created_at", "order": "desc"}, stop=older)


def parse_datetime(value):
	"""«2026-10-01 09:15:00», «2026-10-01T09:15:00+03:00» or «2026-10-01» → naive datetime."""
	if not value:
		return None
	text = str(value).strip()
	try:
		dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
	except ValueError:
		try:
			dt = datetime.fromisoformat(text[:10])
		except ValueError:
			return None
	return dt.replace(tzinfo=None)
