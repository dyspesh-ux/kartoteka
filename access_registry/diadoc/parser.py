"""Parser of the manual «Диадок Общий Реестр» xlsx export.

Columns are found by their header names (normalized), never by position: the header row is
not necessarily the first one, and a legend in the rightmost columns is ignored. The export
normally contains one organization, but a combined registry with many is accepted.
"""

from io import BytesIO

import openpyxl


class ParseError(Exception):
	pass


# Normalized header (lower case, no spaces/dots) -> field name.
HEADER_ALIASES = {
	"компания": "org",
	"фио": "full_name",
	"должность": "position",
	"логин": "login",
	"логинакт": "login_active",
	"админ": "is_admin",
	"заблок": "blocked",
	"подпись": "can_sign",
	"госключ": "gov_key",
	"согл": "can_approve",
	"запроссогл": "can_request_approve",
	"черновики": "drafts",
	"удаление": "can_delete",
	"платежки": "payments",
	"контрагенты": "counterparties",
}
REQUIRED_HEADERS = ("org", "full_name")
FLAG_FIELDS = tuple(k for k in HEADER_ALIASES.values() if k not in REQUIRED_HEADERS + ("position", "login"))
TRUE_WORDS = {"true", "1", "да", "истина", "y", "yes"}
MAX_HEADER_SCAN_ROWS = 10
NO_LOGIN_MARKERS = {"", "—", "-"}


def norm_header(value) -> str:
	return "".join(ch for ch in str(value or "").lower() if not ch.isspace() and ch != ".")


def as_flag(value) -> int:
	if isinstance(value, bool):
		return int(value)
	return int(str(value or "").strip().lower() in TRUE_WORDS)


def parse(data: bytes) -> dict:
	"""xlsx bytes -> {"orgs": [titles], "users": [{org, full_name, position, login, rights}], "warnings"}.

	Duplicate rows of one user key keep the last values and produce a warning.
	"""
	try:
		wb = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
	except Exception as e:
		raise ParseError(f"файл не прочитан как xlsx: {e}") from e
	ws = wb.active
	rows = ws.iter_rows(values_only=True)
	header_map = None
	for idx, row in enumerate(rows, start=1):
		if idx > MAX_HEADER_SCAN_ROWS:
			break
		candidate = {col: HEADER_ALIASES.get(norm_header(value)) for col, value in enumerate(row)}
		candidate = {col: field for col, field in candidate.items() if field}
		if all(field in candidate.values() for field in REQUIRED_HEADERS):
			header_map = candidate
			break
	if header_map is None:
		raise ParseError(
			f"не найдена строка заголовков с колонками «Компания» и «ФИО» в первых "
			f"{MAX_HEADER_SCAN_ROWS} строках"
		)

	orgs: list[str] = []
	users: list[dict] = []
	seen: set[tuple[str, str]] = set()
	warnings: list[str] = []
	for row in rows:
		values = {field: (row[col] if col < len(row) else None) for col, field in header_map.items()}
		org = str(values.get("org") or "").strip()
		full_name = str(values.get("full_name") or "").strip()
		if not org and not full_name:
			continue  # an empty or legend row
		login = str(values.get("login") or "").strip()
		if login.lower() in NO_LOGIN_MARKERS:
			login = ""
		rights = {field: as_flag(values.get(field)) for field in FLAG_FIELDS}
		key = (org, login or full_name)
		if key in seen:
			warnings.append(
				f"в выгрузке {org}: пользователь {login or full_name} встречается несколько раз, взята последняя строка"
			)
		seen.add(key)
		if org not in orgs:
			orgs.append(org)
		users.append(
			{
				"org": org,
				"full_name": full_name,
				"position": str(values.get("position") or "").strip(),
				"login": login,
				"rights": rights,
			}
		)
	if not users:
		raise ParseError("в выгрузке нет строк с данными")
	return {"orgs": orgs, "users": users, "warnings": warnings}
