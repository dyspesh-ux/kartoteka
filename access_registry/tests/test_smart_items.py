"""Edge cases of the smart process mirror: guessing item fields, parsing users and flags, references
that cannot be read, and the error branches of a load. Synthetic data shaped like test_helpdesk."""

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.bitrix24 import smart_items
from access_registry.bitrix24.client import B24Error
from access_registry.bitrix24.smart_items import (
	_flag,
	_unknown,
	_user_id,
	detect_fields,
	hired_stage,
)
from access_registry.bitrix24.smart_refs import _entity_type, ref_id, resolve
from access_registry.tests import test_helpdesk as hd

PORTAL = "si-test"


# --------------------------------------------------------------------------- pure parsing


class TestParsing(FrappeTestCase):
	def test_user_id_variants(self):
		for value, expected in (
			(15, "15"),
			("15", "15"),
			("user_15", "15"),
			([15], "15"),
			([], None),
			(None, None),
			("", None),
			(0, None),
			("0", None),
			("abc", None),
		):
			self.assertEqual(_user_id(value), expected, value)

	def test_unknown_user_label(self):
		self.assertEqual(_unknown(15), "пользователь 15")
		self.assertEqual(_unknown("user_15"), "пользователь 15")
		self.assertIsNone(_unknown(None))
		self.assertIsNone(_unknown(0))
		self.assertIsNone(_unknown([]))

	def test_flag_variants(self):
		for value, expected in (
			("Y", 1),
			("y", 1),
			("1", 1),
			("TRUE", 1),
			("ДА", 1),
			("N", 0),
			("false", 0),
			("", 0),
			(True, 1),
			(False, 0),
			(None, 0),
			(1, 1),
			(0, 0),
		):
			self.assertEqual(_flag(value), expected, value)

	def test_hired_stage_edges(self):
		rows = lambda names: [  # noqa: E731
			{"stage_id": n, "stage_name": n, "state": smart_items.OPEN} for n in names
		]
		# «Оффер принят» is still recruiting, «Собеседования» says nothing
		self.assertIsNone(hired_stage(rows(("Оффер принят", "Собеседования"))))
		# a final stage never means «being hired» even by its name
		done = [{"stage_id": "S", "stage_name": "Принят на работу", "state": smart_items.DONE}]
		self.assertIsNone(hired_stage(done))
		self.assertEqual(hired_stage(rows(("Оформление", "Испытательный срок"))), "Оформление")

	def test_detect_keeps_the_set_fields(self):
		fields = {"ufA": {"type": "employee", "title": "Кто обратился"}}
		codes = detect_fields(fields, {"requester_field": "ufB", "source_field": ""})
		self.assertEqual(codes, {"requester_field": "ufB"})  # set values win, empty ones do not

	def test_detect_title_fallback_and_must_match(self):
		fields = {
			"ufS": {"type": "enumeration", "formLabel": "Источник"},  # no «title»: formLabel is used
			"ufD": {"type": "date", "title": "Когда удобно прийти"},  # no pattern matches
			"ufE": {"type": "employee", "title": "Коллеги"},  # no pattern, but the role may guess
		}
		codes = detect_fields(fields)
		self.assertEqual(codes["source_field"], "ufS")
		self.assertNotIn("deadline_field", codes)  # the title must match for a date field
		self.assertEqual(codes["requester_field"], "ufE")  # any employee field is better than none

	def test_detect_requester_not_multiple(self):
		fields = {
			"ufA": {"type": "employee", "title": "Кто обратился", "isMultiple": True},
			"ufB": {"type": "employee", "title": "Заявитель"},
		}
		self.assertEqual(detect_fields(fields)["requester_field"], "ufB")

	def test_detect_category_never_takes_the_source(self):
		# the source field is set by hand; its title also matches the category patterns, but a field
		# titled «Источник» must stay the source, not become the category
		fields = {"ufS": {"type": "enumeration", "title": "Тип и источник обращения"}}
		codes = detect_fields(fields, {"source_field": "ufS"})
		self.assertNotIn("category_field", codes)

	def test_detect_required_wins_and_system_fields_stay_out(self):
		fields = {
			"ufA": {"type": "date", "title": "Срок"},  # optional
			"ufB": {"type": "date", "title": "Срок решения", "isRequired": True},
			"begindate": {"type": "date", "title": "Плановая дата"},  # system field, never guessed
		}
		codes = detect_fields(fields)
		self.assertEqual(codes["deadline_field"], "ufB")
		self.assertNotIn(codes["deadline_field"], ("ufA", "begindate"))
		self.assertNotIn("done_field", codes)  # «Плановая дата» is not «выполнена/завершена»

	def test_ref_id_variants(self):
		for value, expected in (
			(15, "15"),
			("15", "15"),
			("T43e_15", "15"),
			("DYNAMIC_1086_15", "15"),
			([7, 8], "7"),
			(None, None),
			("", None),
			(0, None),
			("0", None),
			(False, None),
			("abc", None),
		):
			self.assertEqual(ref_id(value), expected, value)

	def test_entity_type_variants(self):
		self.assertEqual(_entity_type({"settings": {"DYNAMIC_1086": "Y"}}, "15"), 1086)
		self.assertEqual(_entity_type({"settings": {}}, "15"), None)
		# several allowed types and a bare id: unknown which one it is
		self.assertEqual(_entity_type({"settings": {"DYNAMIC_1086": "Y", "DYNAMIC_1116": "Y"}}, "15"), None)
		self.assertEqual(_entity_type({"settings": {"DYNAMIC_1086": "N"}}, "15"), None)
		self.assertEqual(_entity_type({}, "T45c_7"), 1116)  # 0x45c, the type is in the value


# --------------------------------------------------------------------------- references


class RefClient:
	"""Answers list_all with a handler; records the calls."""

	def __init__(self, handler):
		self.handler = handler
		self.calls = []

	def list_all(self, method, params, key=None):
		self.calls.append((method, params))
		return self.handler(method, params)


def failing_refs(method, params):
	raise B24Error("ACCESS_DENIED", "Доступ запрещён", method)


class TestRefs(FrappeTestCase):
	def test_unreadable_catalog_and_companies_warn(self):
		fields = {
			"ufPos": {"type": "crm", "title": "Должность", "settings": {"DYNAMIC_1086": "Y"}},
			"mycompanyId": {"type": "crm_company", "title": "Реквизиты вашей компании"},
		}
		items = [{"ufPos": "T43e_5", "mycompanyId": "101"}]  # 0x43e = 1086
		warnings = []
		refs = resolve(
			RefClient(failing_refs),
			fields,
			{"position_field": "ufPos", "organization_field": "mycompanyId"},
			items,
			warnings,
		)
		self.assertTrue(any("Справочник (смарт-процесс 1086) не прочитан" in w for w in warnings))
		self.assertTrue(any("Компании не прочитаны" in w for w in warnings))
		self.assertEqual(refs["ufPos"], {"5": None})  # the registry admits «could not resolve»

	def test_iblock_falls_through_the_list_types(self):
		fields = {
			"ufOffice": {"type": "iblock_element", "title": "Адрес офиса", "settings": {"IBLOCK_ID": 45}}
		}

		def handler(method, params):
			if params["IBLOCK_TYPE_ID"] == "lists":
				raise B24Error("ERROR_IBLOCK_NOT_FOUND", "не найден", method)
			return [{"ID": "900", "NAME": "Москва, Ленина 1"}]

		warnings = []
		refs = resolve(
			RefClient(handler), fields, {"office_field": "ufOffice"}, [{"ufOffice": "900"}], warnings
		)
		self.assertEqual(refs["ufOffice"], {"900": "Москва, Ленина 1"})
		self.assertFalse(warnings)

	def test_iblock_unreadable_shows_numbers(self):
		fields = {
			"ufOffice": {"type": "iblock_element", "title": "Адрес офиса", "settings": {"IBLOCK_ID": 45}}
		}
		warnings = []
		refs = resolve(
			RefClient(failing_refs), fields, {"office_field": "ufOffice"}, [{"ufOffice": "900"}], warnings
		)
		self.assertEqual(refs["ufOffice"], {})
		self.assertTrue(any("Список 45 не прочитан" in w and "lists" in w for w in warnings))

	def test_ambiguous_reference_is_not_read_at_all(self):
		fields = {
			"ufPos": {
				"type": "crm",
				"title": "Должность",
				"settings": {"DYNAMIC_1086": "Y", "DYNAMIC_1116": "Y"},
			}
		}
		client = RefClient(failing_refs)
		warnings = []
		refs = resolve(client, fields, {"position_field": "ufPos"}, [{"ufPos": "5"}], warnings)
		self.assertEqual(refs["ufPos"], {})
		self.assertEqual(client.calls, [])  # no request: the entity cannot be named


# --------------------------------------------------------------------------- the load itself


class TestSmartSync(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in ("B24 Smart Item", "B24 Smart Process"):
			frappe.db.delete(doctype)
		frappe.db.delete("B24 Portal", PORTAL)
		frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": PORTAL,
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
			}
		).insert()
		self.process = frappe.get_doc(
			{"doctype": "B24 Smart Process", "portal": PORTAL, "entity_type_id": hd.ETID}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def sync(self, payload):
		return smart_items.run_process_sync(self.process.name, commit=False, fetch=lambda _doc: payload)

	def test_b24_error_stops_the_load(self):
		def fetch(_doc):
			raise B24Error("ACCESS_DENIED", "Доступ запрещён", "crm.item.list")

		log = smart_items.run_process_sync(self.process.name, commit=False, fetch=fetch)
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("Битрикс24:", log.messages)
		self.assertIn("Вебхуку нужен доступ crm.", log.messages)
		self.assertEqual(frappe.db.count("B24 Smart Item"), 0)
		last_status = frappe.db.get_value("B24 Smart Process", self.process.name, "last_status")
		self.assertTrue(last_status.startswith("Ошибка"), last_status)

	def test_unexpected_error_keeps_the_mirror(self):
		self.assertEqual(self.sync(hd.data()).status, "Успех")
		self.assertEqual(frappe.db.count("B24 Smart Item"), 6)

		def fetch(_doc):
			raise RuntimeError("boom")

		log = smart_items.run_process_sync(self.process.name, commit=False, fetch=fetch)
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("RuntimeError: boom", log.messages)
		self.assertEqual(frappe.db.count("B24 Smart Item"), 6)  # rolled back to the savepoint

	def test_missing_fields_are_warned_about(self):
		payload = hd.data([hd.item(1, "NEW", 1)])
		payload["fields"] = {k: v for k, v in hd.FIELDS.items() if k != hd.SRC}  # no source field
		doc = frappe.get_doc("B24 Smart Process", self.process.name)
		doc.requester_field = "ufCrm9_1999"  # configured, but the process has no such field
		doc.save(ignore_permissions=True)
		log = self.sync(payload)
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertIn("Поля ufCrm9_1999 (requester_field) у смарт-процесса нет", log.messages)
		self.assertIn("Не найдено поле для «Источник обращения»", log.messages)

	def test_row_fallbacks(self):
		rows = [
			dict(hd.item(7, "NEW", 1), title="", **{hd.REQ: 99}),  # unknown requester, no title
			dict(hd.item(8, "GHOST", 1), **{hd.REQ: 0, "assignedById": 0}),  # an unknown stage
		]
		log = self.sync(hd.data(rows))
		self.assertEqual(log.status, "Успех", log.messages)
		name = self.process.name
		seven = frappe.get_doc("B24 Smart Item", f"{name}:7")
		self.assertEqual((seven.title, seven.requester_name), ("#7", "пользователь 99"))
		self.assertIsNone(seven.requester_b24)
		eight = frappe.get_doc("B24 Smart Item", f"{name}:8")
		self.assertEqual((eight.stage_name, eight.state), ("DT1300_5:GHOST", "Открыта"))
		self.assertIsNone(eight.requester_name)

	def test_no_url_without_the_portal_address(self):
		frappe.db.delete("B24 Portal", "si-masked")
		frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": "si-masked",
				"title": "Маскированный",
				"webhook": "****",  # the secret is masked: the portal address cannot be derived
			}
		).insert()
		process = frappe.get_doc(
			{"doctype": "B24 Smart Process", "portal": "si-masked", "entity_type_id": hd.ETID}
		).insert()
		log = smart_items.run_process_sync(
			process.name, commit=False, fetch=lambda _doc: hd.data([hd.item(1, "NEW", 1)])
		)
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertIsNone(frappe.db.get_value("B24 Smart Item", f"{process.name}:1", "url"))


# --------------------------------------------------------------------------- reading


class TypesClient:
	"""A portal with the helpdesk process; records the parameters of the item requests."""

	def __init__(self, stages_as_open=None, closed_rows=None):
		self.stages_as_open = stages_as_open if stages_as_open is not None else [hd.item(1, "NEW", 1)]
		self.closed_rows = closed_rows or []
		self.requests = []

	def crm_types(self):
		return [{"entityTypeId": hd.ETID, "title": "Заявки технической поддержки"}]

	def result(self, method, params):
		if method == "crm.item.fields":
			return {"fields": hd.FIELDS}
		if method == "crm.category.list":
			return {"categories": [{"id": 5, "name": "Общая"}]}
		raise AssertionError(method)

	def call_many(self, method, param_list):
		assert method == "crm.status.list"
		return [hd.STAGES]

	def list_all(self, method, params, key=None):
		self.requests.append(params)
		if ">=movedTime" in params.get("filter", {}):
			return self.closed_rows
		return self.stages_as_open


class TestFetchProcess(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("B24 Portal", PORTAL)
		frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": PORTAL,
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
			}
		).insert()
		self.process = frappe.get_doc(
			{"doctype": "B24 Smart Process", "portal": PORTAL, "entity_type_id": hd.ETID}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_process_not_on_the_portal(self):
		client = TypesClient()
		client.crm_types = lambda: [{"entityTypeId": 999, "title": "Другой"}]
		with self.assertRaises(B24Error) as ctx:
			smart_items.fetch_process(self.process, client)
		self.assertIn("1300", str(ctx.exception))

	def test_no_stages_reads_everything(self):
		class NoStages(TypesClient):
			def result(self, method, params):
				if method == "crm.category.list":
					return {"categories": []}
				return super().result(method, params)

		client = NoStages()
		data = smart_items.fetch_process(self.process, client)
		self.assertEqual(len(data["items"]), 1)
		self.assertEqual(len(client.requests), 1)  # one request, no split into open/closed
		self.assertNotIn("filter", client.requests[0])  # and no stage filter at all

	def test_item_closed_between_two_requests_is_closed(self):
		# the same item id in the answers of the open and the closed request: the later one wins
		closed = [dict(hd.item(1, "SUCCESS", 1, moved_days_ago=0))]
		data = smart_items.fetch_process(self.process, TypesClient(closed_rows=closed))
		self.assertEqual(len(data["items"]), 1)
		self.assertTrue(data["items"][0]["stageId"].endswith("SUCCESS"))
