"""Read-only PostgreSQL client for ZKBio CVAccess (biosecurity-boot).

The registry never writes to the ACS database: the session is opened with
default_transaction_read_only=on, and production deployments should use a role
with SELECT-only grants (see README, «СКУД»).
"""

import frappe

CONNECT_TIMEOUT = 10
STATEMENT_TIMEOUT_MS = 60000

PERSONS_SQL = """
select p.id, p.pin, p.last_name, p.name as first_name, p.email, p.hire_date, p.status,
	p.update_time, d.name as dept_name
from pers_person p
left join auth_department d on d.id = p.auth_dept_id
"""

EVENTS_SQL = """
select id, event_time, pin, card_no, dev_alias, event_point_name, reader_name,
	event_no, event_name, verify_mode_name, acc_zone, area_name
from acc_transaction
where event_time > %(since)s
order by event_time
limit %(limit)s
"""


def _psycopg2():
	"""Imports the driver lazily: the app must install without it; the error must be actionable."""
	try:
		import psycopg2
		import psycopg2.extras
	except ImportError:
		frappe.throw(
			"Драйвер PostgreSQL (psycopg2-binary) не установлен на сайте — см. README, раздел «СКУД»."
		)
	return psycopg2, psycopg2.extras


def _connect(server):
	psycopg2, _extras = _psycopg2()
	conn = psycopg2.connect(
		host=server.host,
		port=server.port or 5442,
		dbname=server.database,
		user=server.username,
		password=server.get_password("password"),
		connect_timeout=CONNECT_TIMEOUT,
		options=f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}",
	)
	conn.set_session(readonly=True)
	return conn


def _query(server, sql, params=None) -> list[dict]:
	_psycopg2_module, extras = _psycopg2()
	conn = _connect(server)
	try:
		with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
			cur.execute(sql, params or {})
			return [dict(row) for row in cur.fetchall()]
	finally:
		conn.close()


def test_connection(server) -> dict:
	version = _query(server, "select version() as v")[0]["v"]
	persons = _query(server, "select count(*) as n from pers_person")[0]["n"]
	events = _query(server, "select count(*) as n from acc_transaction")[0]["n"]
	return {"persons": persons, "events": events, "version": version.split(",")[0]}


def fetch_persons(server) -> list[dict]:
	return _query(server, PERSONS_SQL)


def fetch_events(server, since, limit) -> list[dict]:
	return _query(server, EVENTS_SQL, {"since": since, "limit": limit})
