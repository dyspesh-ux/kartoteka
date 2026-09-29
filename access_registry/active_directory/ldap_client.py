"""Read-only LDAP access to Active Directory (ldap3, part of the Frappe dependencies).

``fetch_directory`` returns plain dicts with normalized attribute values, the same shape the
tests use as fixtures, so the import never sees ldap3 objects.
"""

import datetime
import ssl
import uuid

import frappe
from frappe import _

USER_ATTRIBUTES = [
	"objectGUID",
	"distinguishedName",
	"sAMAccountName",
	"userPrincipalName",
	"displayName",
	"givenName",
	"sn",
	"middleName",
	"mail",
	"title",
	"department",
	"company",
	"manager",
	"employeeNumber",
	"employeeID",
	"userAccountControl",
	"lockoutTime",
	"pwdLastSet",
	"lastLogonTimestamp",
	"accountExpires",
	"whenCreated",
	"whenChanged",
	"memberOf",
]
GROUP_ATTRIBUTES = [
	"objectGUID",
	"distinguishedName",
	"cn",
	"sAMAccountName",
	"description",
	"groupType",
	"managedBy",
	"memberOf",
	"whenCreated",
	"whenChanged",
]
MULTI_VALUED = {"memberOf"}
PAGE_SIZE = 500
NEVER = {0, 0x7FFFFFFFFFFFFFFF}
FILETIME_EPOCH = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


# --------------------------------------------------------------------------- value conversion


def guid_to_str(value) -> str:
	"""objectGUID as a canonical UUID string (AD stores it little-endian)."""
	if isinstance(value, list):
		value = value[0] if value else None
	if value is None:
		return ""
	if isinstance(value, bytes | bytearray):
		return str(uuid.UUID(bytes_le=bytes(value)))
	return str(value).strip("{}").lower()


def filetime_to_datetime(value) -> datetime.datetime | None:
	"""AD FILETIME (100 ns since 1601) or a datetime → naive UTC datetime; «never» → None."""
	if value in (None, "", []):
		return None
	if isinstance(value, datetime.datetime):
		if value.tzinfo:
			value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
		return None if value.year <= 1601 or value.year >= 9999 else value
	try:
		number = int(value)
	except (TypeError, ValueError):
		return None
	if number in NEVER or number < 0:
		return None
	return (FILETIME_EPOCH + datetime.timedelta(microseconds=number // 10)).replace(tzinfo=None)


def generalized_time(value) -> datetime.datetime | None:
	"""whenCreated/whenChanged: GeneralizedTime string or datetime → naive UTC datetime."""
	if value in (None, "", []):
		return None
	if isinstance(value, datetime.datetime):
		return filetime_to_datetime(value)
	text = str(value).rstrip("Z").split(".")[0]
	try:
		return datetime.datetime.strptime(text[:14], "%Y%m%d%H%M%S")
	except ValueError:
		return None


def normalize(attributes: dict, names: list) -> dict:
	"""ldap3 attributes → plain JSON-friendly dict: single values unwrapped, times as ISO strings."""
	result = {}
	for name in names:
		value = attributes.get(name)
		if name in MULTI_VALUED:
			result[name] = sorted(str(v) for v in (value or []))
			continue
		if isinstance(value, list):
			value = value[0] if value else None
		if name == "objectGUID":
			value = guid_to_str(value)
		elif name in ("lockoutTime", "pwdLastSet", "lastLogonTimestamp", "accountExpires"):
			dt = filetime_to_datetime(value)
			value = dt.isoformat() if dt else None
		elif name in ("whenCreated", "whenChanged"):
			dt = generalized_time(value)
			value = dt.isoformat() if dt else None
		elif name in ("userAccountControl", "groupType"):
			value = int(value) if value not in (None, "") else 0
		elif isinstance(value, bytes):
			value = value.decode("utf-8", "replace")
		elif value is not None:
			value = str(value)
		result[name] = value
	return result


# --------------------------------------------------------------------------- connection


def connect(domain):
	import ldap3

	urls = (domain.ldap_url or "").split()
	if not urls:
		frappe.throw(_("У домена {0} не указан сервер LDAP").format(domain.name))
	tls = ldap3.Tls(validate=ssl.CERT_REQUIRED if domain.verify_ssl else ssl.CERT_NONE)
	servers = [
		ldap3.Server(url, use_ssl=url.lower().startswith("ldaps"), tls=tls, get_info=ldap3.SCHEMA)
		for url in urls
	]
	pool = (
		ldap3.ServerPool(servers, ldap3.FIRST, active=True, exhaust=True) if len(servers) > 1 else servers[0]
	)
	password = domain.get_password("bind_password", raise_exception=False) if domain.bind_password else ""
	return ldap3.Connection(
		pool,
		user=domain.bind_user,
		password=password,
		authentication=ldap3.SIMPLE,
		read_only=True,
		auto_bind=True,
		receive_timeout=120,
	)


def _search(conn, bases, search_filter, attributes):
	rows = []
	for base in bases:
		entries = conn.extend.standard.paged_search(
			search_base=base,
			search_filter=search_filter,
			attributes=attributes,
			paged_size=PAGE_SIZE,
			generator=True,
		)
		for entry in entries:
			if entry.get("type") != "searchResEntry":
				continue
			attrs = dict(entry.get("attributes") or {})
			# objectGUID is binary: always take the raw bytes, whatever the schema formatting did
			raw_guid = (entry.get("raw_attributes") or {}).get("objectGUID")
			if raw_guid:
				attrs["objectGUID"] = raw_guid[0]
			rows.append(normalize(attrs, attributes))
	return rows


def _bases(text, base_dn):
	bases = [line.strip() for line in (text or "").splitlines() if line.strip()]
	return bases or [base_dn]


def fetch_directory(domain, connection=None) -> dict:
	"""All users and groups of the domain (read-only). ``connection`` is for tests (ldap3 MOCK_SYNC)."""
	conn = connection or connect(domain)
	try:
		users = _search(
			conn, _bases(domain.user_search_bases, domain.base_dn), domain.user_filter, USER_ATTRIBUTES
		)
		groups = _search(
			conn, _bases(domain.group_search_bases, domain.base_dn), domain.group_filter, GROUP_ATTRIBUTES
		)
	finally:
		conn.unbind()
	return {"users": users, "groups": groups}


def test_connection(domain) -> str:
	try:
		conn = connect(domain)
	except Exception as e:
		return _("Не удалось подключиться: {0}").format(frappe.utils.escape_html(str(e)))
	try:
		conn.search(domain.base_dn, domain.user_filter, attributes=["sAMAccountName"], size_limit=1)
		found = len(conn.entries)
		who = conn.extend.standard.who_am_i() or domain.bind_user
	finally:
		conn.unbind()
	return _("Подключение работает. Вошли как {0}, пробный поиск учёток: {1}.").format(
		frappe.utils.escape_html(str(who)),
		_("найдено") if found else _("ничего не найдено — проверьте Base DN и фильтр"),
	)
