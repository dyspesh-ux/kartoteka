"""Unit tests for the sync helpers: normalisation keys, person state, merge candidates and client.

Pure functions are checked directly; DB-touching helpers build minimal Person documents
instead of running a full ZUP fixture sync (test_zup_sync.py covers the end-to-end flows).
"""

import datetime
from types import SimpleNamespace
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from access_registry.sync import client
from access_registry.sync.client import SourceFetchError, _as_list, _get, _session
from access_registry.sync.departments import ROOT_KEY, ROOT_TITLE, ensure_root, org_node_key
from access_registry.sync.normalize import comparable, date_or_none, iso_date, match_keys, normalize_name
from access_registry.sync.persons import (
	REASON_FULL_MATCH,
	REASON_PARTIAL_MATCH,
	compute_person_state,
	create_merge_candidate,
	merge_persons,
	pair_key,
	propose_candidates,
	rescan_merge_candidates,
)

CLN = "CLN1"


def emp(status="Работает", kind="ОсновноеМестоРаботы", category="Работа", working=True):
	"""One employment row as seen by compute_person_state (attribute access only)."""
	return SimpleNamespace(
		status=status, employment_kind_code=kind, category=category, actually_working=working
	)


class TestNormalize(FrappeTestCase):
	def test_normalize_name_empty_and_blank(self):
		self.assertEqual(normalize_name(None), "")
		self.assertEqual(normalize_name(""), "")

	def test_normalize_name_collapses_spaces_and_case(self):
		self.assertEqual(normalize_name("  Иванов   Иван "), "иванов иван")
		self.assertEqual(normalize_name("Иванов\n\t Иван"), "иванов иван")
		self.assertEqual(normalize_name("Фёдоров"), "федоров")

	def test_normalize_name_latin_lookalikes(self):
		self.assertEqual(normalize_name("Aндрeв"), normalize_name("Андрев"))
		self.assertEqual(normalize_name("Cмирнoв"), "смирнов")
		self.assertEqual(normalize_name("Bcex"), "всех")
		# digits and unmatched characters pass through
		self.assertEqual(normalize_name("Отдел №7"), "отдел №7")

	def test_match_keys_full_and_partial(self):
		full, partial = match_keys("Иванов", "Иван", "Иванович", "1980-01-15")
		self.assertEqual(full, "иванов|иван|иванович|1980-01-15")
		self.assertEqual(partial, "иван|иванович|1980-01-15")

	def test_match_keys_require_birth_date_and_first_name(self):
		self.assertEqual(match_keys("Иванов", "Иван", "Иванович", None), ("", ""))
		self.assertEqual(match_keys("Иванов", "Иван", "Иванович", ""), ("", ""))
		self.assertEqual(match_keys("Иванов", "", "Иванович", "1980-01-15"), ("", ""))
		self.assertEqual(match_keys("Иванов", None, None, "1980-01-15"), ("", ""))

	def test_match_keys_normalised_and_middle_name_optional(self):
		full, partial = match_keys("Фёдоров", "Aлeксей", None, "1990-07-20")
		self.assertEqual(full, "федоров|алексей||1990-07-20")
		self.assertEqual(partial, "алексей||1990-07-20")

	def test_match_keys_accept_datetime_birth_date(self):
		by_date, _ = match_keys("Иванов", "Иван", "Иванович", getdate("1980-01-15"))
		by_datetime, _ = match_keys("Иванов", "Иван", "Иванович", datetime.datetime(1980, 1, 15, 12, 30))
		self.assertEqual(by_datetime, by_date)

	def test_iso_date(self):
		self.assertEqual(iso_date(None), "")
		self.assertEqual(iso_date(""), "")
		self.assertEqual(iso_date(getdate("1980-01-15")), "1980-01-15")
		self.assertEqual(iso_date(datetime.datetime(1980, 1, 15, 23, 59)), "1980-01-15")
		self.assertEqual(iso_date("1980-01-15"), "1980-01-15")

	def test_date_or_none(self):
		self.assertIsNone(date_or_none(None))
		self.assertIsNone(date_or_none(""))
		self.assertEqual(date_or_none("1980-01-15"), getdate("1980-01-15"))
		# the 1C empty date: getdate() returns None for it in Frappe v15
		self.assertIsNone(date_or_none("0001-01-01"))

	def test_comparable_check_and_int(self):
		self.assertEqual(comparable("1", "Check"), 1)
		self.assertEqual(comparable(0, "Check"), 0)
		self.assertEqual(comparable(None, "Check"), 0)
		self.assertEqual(comparable("5", "Int"), 5)

	def test_comparable_numeric_fieldtypes(self):
		self.assertEqual(comparable(0.123456789, "Float"), 0.123457)
		self.assertEqual(comparable("10.10", "Currency"), 10.1)
		self.assertEqual(comparable("25", "Percent"), 25)

	def test_comparable_date_and_datetime(self):
		self.assertEqual(comparable(getdate("1980-01-15"), "Date"), "1980-01-15")
		self.assertEqual(comparable("1980-01-15", "Date"), "1980-01-15")
		self.assertEqual(comparable(None, "Datetime"), "")
		self.assertEqual(
			comparable(datetime.datetime(2026, 6, 1, 8, 0), "Datetime"),
			str(datetime.datetime(2026, 6, 1, 8, 0)),
		)

	def test_comparable_plain_values(self):
		self.assertEqual(comparable(None, "Data"), "")
		self.assertEqual(comparable("abc", "Data"), "abc")
		self.assertEqual(comparable(7, "Data"), "7")


class TestPersonState(FrappeTestCase):
	def test_no_employments(self):
		self.assertEqual(
			compute_person_state([]),
			{"status": "", "presence": "На месте", "external_part_time_only": 0},
		)

	def test_active_employment(self):
		state = compute_person_state([emp()])
		self.assertEqual(state, {"status": "Работает", "presence": "На месте", "external_part_time_only": 0})

	def test_terminating_still_counts_as_active(self):
		state = compute_person_state([emp(status="Увольняется")])
		self.assertEqual(state["status"], "Работает")

	def test_not_hired_wins_over_terminated(self):
		state = compute_person_state([emp(status="Не принят"), emp(status="Уволен")])
		self.assertEqual(state["status"], "Не принят")

	def test_all_missing_from_export(self):
		state = compute_person_state([emp(status="Нет в выгрузке"), emp(status="Нет в выгрузке")])
		self.assertEqual(state["status"], "Нет в выгрузке")

	def test_missing_mixed_with_terminated_is_terminated(self):
		state = compute_person_state([emp(status="Нет в выгрузке"), emp(status="Уволен")])
		self.assertEqual(state["status"], "Уволен")

	def test_terminated(self):
		self.assertEqual(compute_person_state([emp(status="Уволен")])["status"], "Уволен")

	def test_parental_leave_of_every_active_job_is_long_absence(self):
		state = compute_person_state([emp(category="ОтпускПоУходу", working=False)])
		self.assertEqual(state["status"], "Работает")
		self.assertEqual(state["presence"], "Длительное отсутствие")

	def test_one_working_active_job_keeps_presence(self):
		state = compute_person_state(
			[emp(category="ОтпускПоУходу", working=False), emp(category="Работа", working=True)]
		)
		self.assertEqual(state["presence"], "На месте")

	def test_parental_leave_with_actual_work_keeps_presence(self):
		state = compute_person_state([emp(category="ОтпускПоУходу", working=True)])
		self.assertEqual(state["presence"], "На месте")

	def test_external_part_time_flag(self):
		only_part_time = [emp(kind="Совместительство")]
		self.assertEqual(compute_person_state(only_part_time)["external_part_time_only"], 1)
		with_main = [emp(kind="Совместительство"), emp(kind="ОсновноеМестоРаботы")]
		self.assertEqual(compute_person_state(with_main)["external_part_time_only"], 0)
		inactive_only = [emp(status="Уволен", kind="Совместительство")]
		self.assertEqual(compute_person_state(inactive_only)["external_part_time_only"], 0)

	def test_pair_key_is_order_independent(self):
		self.assertEqual(pair_key("b", "a"), "a|b")
		self.assertEqual(pair_key("a", "b"), pair_key("b", "a"))


DB_DOCTYPES = [
	"Person Merge Candidate",
	"Employment",
	"Person Source ID",
	"Person",
]


def make_person(last_name="Смирнов", first_name="Олег", middle_name="Петрович", birth_date="1985-03-10"):
	return frappe.get_doc(
		{
			"doctype": "Person",
			"last_name": last_name,
			"first_name": first_name,
			"middle_name": middle_name,
			"birth_date": birth_date,
		}
	).insert(ignore_permissions=True)


class TestPersonDb(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in DB_DOCTYPES + ["HR Department"]:
			frappe.db.delete(doctype)
		frappe.db.delete("Comment", {"reference_doctype": "Person"})
		frappe.db.delete("Deleted Document", {"deleted_doctype": "Person"})
		frappe.db.delete("Version", {"ref_doctype": ["in", DB_DOCTYPES]})
		frappe.db.delete("Info Base", {"source_code": ["like", "CLN%"]})
		ensure_root()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ------------------------------------------------------------------ departments

	def test_ensure_root_is_idempotent(self):
		self.assertEqual(frappe.db.count("HR Department"), 1)
		self.assertEqual(ensure_root(), ROOT_KEY)
		self.assertEqual(ensure_root(), ROOT_KEY)
		self.assertEqual(frappe.db.count("HR Department"), 1)
		root = frappe.get_doc("HR Department", ROOT_KEY)
		self.assertEqual(root.title, ROOT_TITLE)
		self.assertEqual(root.node_type, "Корень")
		self.assertTrue(root.is_group)

	def test_org_node_key_format(self):
		self.assertEqual(org_node_key(CLN, "abc-123"), f"{CLN}:org:abc-123")

	# ------------------------------------------------------------------ create / propose

	def test_create_merge_candidate_rejects_self_pair(self):
		person = make_person()
		self.assertFalse(create_merge_candidate(person.name, person.name, REASON_FULL_MATCH))
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 0)

	def test_create_merge_candidate_and_pair_dedup(self):
		a = make_person()
		b = make_person()
		self.assertTrue(create_merge_candidate(a.name, b.name, REASON_FULL_MATCH))
		candidate = frappe.get_doc("Person Merge Candidate", {"person_a": a.name})
		self.assertEqual(candidate.person_b, b.name)
		self.assertEqual(candidate.status, "Открыт")
		self.assertEqual(candidate.pair_key, pair_key(a.name, b.name))
		# the same pair in any order is not suggested again
		self.assertFalse(create_merge_candidate(a.name, b.name, REASON_FULL_MATCH))
		self.assertFalse(create_merge_candidate(b.name, a.name, REASON_PARTIAL_MATCH))
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 1)

	def test_create_merge_candidate_respects_different_people(self):
		a = make_person()
		b = make_person()
		create_merge_candidate(a.name, b.name, REASON_FULL_MATCH)
		frappe.get_doc("Person Merge Candidate", {"person_a": a.name}).mark_different()
		self.assertFalse(create_merge_candidate(a.name, b.name, REASON_FULL_MATCH))
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 1)

	def test_propose_candidates_full_and_partial(self):
		a = make_person()
		b = make_person()  # identical to A: full match
		self.assertEqual(propose_candidates(b.name, b.match_key, b.match_key_partial), 1)
		full = frappe.get_doc("Person Merge Candidate", {"person_b": b.name})
		self.assertEqual(full.person_a, a.name)
		self.assertEqual(full.reason, REASON_FULL_MATCH)

		c = make_person(last_name="Смирнова")  # same first name, middle name and birth date
		self.assertEqual(propose_candidates(c.name, c.match_key, c.match_key_partial), 2)
		partial = frappe.get_all(
			"Person Merge Candidate",
			filters={"person_b": c.name, "reason": REASON_PARTIAL_MATCH},
			pluck="person_a",
		)
		self.assertEqual(sorted(partial), sorted([a.name, b.name]))

		# D is a full duplicate of C and a partial match for A and B: three new pairs
		d = make_person(last_name="Смирнова")
		self.assertEqual(propose_candidates(d.name, d.match_key, d.match_key_partial), 3)
		self.assertTrue(
			frappe.db.exists(
				"Person Merge Candidate",
				{"person_a": c.name, "person_b": d.name, "reason": REASON_FULL_MATCH},
			)
		)
		# a repeated proposal for the same person suggests nothing new
		self.assertEqual(propose_candidates(d.name, d.match_key, d.match_key_partial), 0)
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 6)

	def test_propose_candidates_without_keys_suggests_nothing(self):
		make_person(birth_date=None)
		person = make_person(birth_date=None)
		self.assertFalse(person.match_key)
		self.assertEqual(propose_candidates(person.name, "", ""), 0)
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 0)

	def test_propose_candidates_excludes_the_person_itself(self):
		person = make_person()
		self.assertEqual(propose_candidates(person.name, person.match_key, person.match_key_partial), 0)
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 0)

	def test_propose_candidates_full_match_is_not_duplicated_as_partial(self):
		make_person()
		b = make_person()  # same full key: the partial pass must not add a second candidate
		self.assertEqual(propose_candidates(b.name, b.match_key, b.match_key_partial), 1)
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 1)

	# ------------------------------------------------------------------ rescan

	def test_rescan_skips_persons_without_match_keys(self):
		make_person(birth_date=None)
		make_person(birth_date=None)
		self.assertEqual(rescan_merge_candidates(), {"full": 0, "partial": 0})

	def test_rescan_full_key_trio(self):
		make_person()
		make_person()
		make_person()
		# three persons with one full key: C(3, 2) = 3 pairs, no partial duplicates
		self.assertEqual(rescan_merge_candidates(), {"full": 3, "partial": 0})
		self.assertEqual(frappe.db.count("Person Merge Candidate"), 3)

	def test_rescan_mixed_full_and_partial_groups(self):
		make_person()
		make_person()  # full pair with the first one
		make_person(last_name="Смирнова")  # partial pair with each of them
		self.assertEqual(rescan_merge_candidates(), {"full": 1, "partial": 2})
		# a repeated run suggests nothing new
		self.assertEqual(rescan_merge_candidates(), {"full": 0, "partial": 0})

	# ------------------------------------------------------------------ merge

	def _person_with_sources(self, source_rows):
		return frappe.get_doc(
			{
				"doctype": "Person",
				"last_name": "Смирнов",
				"first_name": "Олег",
				"middle_name": "Петрович",
				"birth_date": "1985-03-10",
				"source_ids": source_rows,
			}
		).insert(ignore_permissions=True)

	def test_merge_moves_only_new_source_ids(self):
		# Person Source ID.source is a Link to Info Base: the fixture sources must exist
		for code in ("CLN1", "CLN2"):
			frappe.get_doc(
				{
					"doctype": "Info Base",
					"source_code": code,
					"title": code,
					"base_url": "http://127.0.0.1:9/hs",
				}
			).insert()
		keep = self._person_with_sources([{"source": "CLN1", "person_guid": "g1"}])
		drop = self._person_with_sources(
			[
				{"source": "CLN1", "person_guid": "g1"},  # already on «keep»: must not be duplicated
				{"source": "CLN2", "person_guid": "g2"},
			]
		)
		merge_persons(keep.name, drop.name)
		survivor = frappe.get_doc("Person", keep.name)
		self.assertEqual(
			sorted((row.source, row.person_guid) for row in survivor.source_ids),
			[("CLN1", "g1"), ("CLN2", "g2")],
		)
		self.assertEqual([row.idx for row in survivor.source_ids], [1, 2])
		self.assertFalse(frappe.db.exists("Person", drop.name))

	def test_merge_recomputes_state_from_repointed_employments(self):
		keep = make_person()
		drop = make_person()
		frappe.get_doc(
			{
				"doctype": "Employment",
				"record_key": f"{CLN}:emp-1",
				"person": drop.name,
				"status": "Уволен",
				"employment_kind_code": "ОсновноеМестоРаботы",
				"category": "Работа",
				"actually_working": 1,
			}
		).insert(ignore_permissions=True)

		merge_persons(keep.name, drop.name)

		self.assertEqual(frappe.db.get_value("Employment", f"{CLN}:emp-1", "person"), keep.name)
		survivor = frappe.get_doc("Person", keep.name)
		self.assertEqual(survivor.status, "Уволен")
		self.assertEqual(survivor.presence, "На месте")

	def test_merge_marks_candidate_as_merged(self):
		keep = make_person()
		drop = make_person()
		self.assertTrue(create_merge_candidate(keep.name, drop.name, REASON_FULL_MATCH))
		candidate = frappe.get_doc("Person Merge Candidate", {"person_a": keep.name})
		merge_persons(keep.name, drop.name, candidate=candidate.name)
		candidate.reload()
		self.assertEqual(candidate.status, "Склеено")
		self.assertEqual(candidate.merged_uuid, drop.name)

	def test_merge_collapses_reverse_candidate(self):
		# The candidate stored as (drop, keep): after the merge both sides point to «keep»
		keep = make_person()
		drop = make_person()
		self.assertTrue(create_merge_candidate(drop.name, keep.name, REASON_FULL_MATCH))
		candidate = frappe.get_doc("Person Merge Candidate", {"person_a": drop.name})
		merge_persons(keep.name, drop.name, candidate=candidate.name)
		candidate.reload()
		self.assertEqual(candidate.status, "Склеено")
		self.assertEqual(candidate.merged_uuid, drop.name)
		self.assertEqual(candidate.pair_key, f"{keep.name}|{drop.name}|{candidate.name}")

	def test_merge_resolves_overlapping_candidates(self):
		keep = make_person()
		drop = make_person()
		x = make_person(first_name="Григорий")
		self.assertTrue(create_merge_candidate(x.name, keep.name, REASON_FULL_MATCH))
		self.assertTrue(create_merge_candidate(x.name, drop.name, REASON_FULL_MATCH))
		other = frappe.get_doc("Person Merge Candidate", {"person_b": keep.name})
		duplicate = frappe.get_doc("Person Merge Candidate", {"person_b": drop.name})
		duplicate.mark_different()

		merge_persons(keep.name, drop.name)

		# the pair collapsed onto the existing candidate: «Разные люди» wins, the duplicate is removed
		other.reload()
		self.assertEqual(other.status, "Разные люди")
		self.assertEqual(other.pair_key, pair_key(x.name, keep.name))
		self.assertFalse(frappe.db.exists("Person Merge Candidate", duplicate.name))


class _FakeResponse:
	def __init__(self, status_code=200, payload=None, text="", json_ok=True):
		self.status_code = status_code
		self._payload = payload
		self.text = text
		self._json_ok = json_ok
		self.encoding = None

	def json(self):
		if not self._json_ok:
			raise ValueError("No JSON object could be decoded")
		return self._payload


class _FakeSession:
	def __init__(self, response):
		self.response = response
		self.calls = []

	def get(self, url, params=None, timeout=None):
		self.calls.append({"url": url, "params": params, "timeout": timeout})
		return self.response


class TestSourceClient(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Info Base", {"source_code": ["like", "CLN%"]})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ------------------------------------------------------------------ _as_list

	def test_as_list_accepts_empty_list_and_none(self):
		self.assertEqual(_as_list(None), [])
		self.assertEqual(_as_list([]), [])

	def test_as_list_passthrough_and_wrappers(self):
		payload = [{"a": 1}]
		self.assertEqual(_as_list(payload), payload)
		for key in ("data", "items", "value", "result"):
			self.assertEqual(_as_list({key: payload}), payload, key)
		# a single list under any other key is accepted too
		self.assertEqual(_as_list({"rows": payload}), payload)

	def test_as_list_rejects_bad_payloads(self):
		self.assertRaises(SourceFetchError, _as_list, {})
		self.assertRaises(SourceFetchError, _as_list, {"a": 1})
		self.assertRaises(SourceFetchError, _as_list, {"a": [], "b": [1]})
		self.assertRaises(SourceFetchError, _as_list, "nonsense")
		self.assertRaises(SourceFetchError, _as_list, 42)

	# ------------------------------------------------------------------ _get

	def test_get_success(self):
		session = _FakeSession(_FakeResponse(payload={"ok": 1}))
		result = _get(session, SimpleNamespace(base_url="http://zup.example/hs/"), "employees", timeout=13)
		self.assertEqual(result, {"ok": 1})
		self.assertEqual(session.calls[0]["url"], "http://zup.example/hs/employees")
		self.assertEqual(session.calls[0]["timeout"], 13)

	def test_get_http_error(self):
		session = _FakeSession(_FakeResponse(status_code=500, text="server exploded"))
		with self.assertRaises(SourceFetchError) as ctx:
			_get(session, SimpleNamespace(base_url="http://zup.example/hs"), "meta")
		self.assertIn("500", str(ctx.exception))
		self.assertIn("server exploded", str(ctx.exception))

	def test_get_invalid_json(self):
		session = _FakeSession(_FakeResponse(text="<html>login page</html>", json_ok=False))
		with self.assertRaises(SourceFetchError) as ctx:
			_get(session, SimpleNamespace(base_url="http://zup.example/hs"), "meta")
		self.assertIn("не JSON", str(ctx.exception))
		self.assertIn("<html>", str(ctx.exception))

	# ------------------------------------------------------------------ _session / fetch_source_data

	def _info_base(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Info Base",
				"source_code": CLN,
				"title": CLN,
				"base_url": "https://zup.example/hs",
				**values,
			}
		).insert()

	def test_session_auth_verify_and_headers(self):
		source = self._info_base(username="svc", password="s3cret", verify_ssl=0)
		session = _session(source)
		self.assertEqual(session.auth, ("svc", "s3cret"))
		self.assertFalse(session.verify)
		self.assertEqual(session.headers["Accept"], "application/json")

	def test_session_without_username_has_no_auth(self):
		# insert fills the svc_hr_export field default, so null the attribute in memory:
		# _session only reads it, and a source without a username must not get auth
		source = self._info_base()
		source.username = None
		session = _session(source)
		self.assertIsNone(session.auth)
		self.assertTrue(session.verify)  # the field default

	def test_fetch_source_data_continues_after_meta_failure(self):
		source = self._info_base()

		def fake_get(session, source, path, params=None, timeout=None):
			if path == "meta":
				raise ConnectionError("boom")
			if path == "organizations":
				return [{"org": 1}]
			if path == "departments":
				return {"data": [{"dep": 1}]}
			if path == "employees":
				return [{"emp": 1}]
			return [{"abs": 1}]

		with mock.patch.object(client, "_get", side_effect=fake_get) as get_mock:
			data = client.fetch_source_data(source, getdate("2026-06-01"), getdate("2026-06-02"), timeout=7)
		# the meta failure is reported, the other endpoints are still fetched
		self.assertIsNone(data["meta"])
		self.assertIn("ConnectionError", data["meta_error"])
		self.assertIn("boom", data["meta_error"])
		self.assertEqual(data["organizations"], [{"org": 1}])
		self.assertEqual(data["departments"], [{"dep": 1}])  # the wrapper object is unwrapped
		self.assertEqual(data["employees"], [{"emp": 1}])
		self.assertEqual(data["absences"], [{"abs": 1}])

		endpoints = [call.args[2] for call in get_mock.call_args_list]
		self.assertEqual(endpoints, ["meta", "organizations", "departments", "employees", "absences"])
		absences_call = get_mock.call_args_list[endpoints.index("absences")]
		self.assertEqual(absences_call.kwargs["params"], {"from": "2026-06-01", "to": "2026-06-02"})
		self.assertEqual(absences_call.kwargs["timeout"], 7)

	def test_fetch_source_data_fails_when_a_regular_endpoint_errors(self):
		source = self._info_base()

		def fake_get(session, source, path, params=None, timeout=None):
			if path == "employees":
				raise SourceFetchError("GET … → HTTP 503")
			return []

		with mock.patch.object(client, "_get", side_effect=fake_get):
			with self.assertRaises(SourceFetchError):
				client.fetch_source_data(source, getdate("2026-06-01"), getdate("2026-06-02"), timeout=5)
