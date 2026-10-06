"""Helpdesk snapshot: items of a Bitrix24 smart process (synthetic data), the section «Техподдержка»."""

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from access_registry import app_access
from access_registry.bitrix24 import smart_items, support
from access_registry.registry import api
from access_registry.tests.test_permissions import make_user

PORTAL = "hd-test"
ETID = 1300
REQ, CAT, SRC, DUE, DONE = (
	"ufCrm9_1001",
	"ufCrm9_1002",
	"ufCrm9_1003",
	"ufCrm9_1004",
	"ufCrm9_1005",
)
FIELDS = {
	"id": {"type": "integer", "title": "ID"},
	"title": {"type": "string", "title": "Название"},
	"assignedById": {"type": "user", "title": "Ответственный"},
	"observers": {"type": "user", "title": "Наблюдатели", "isMultiple": True},
	"ufCrm9_0999": {"type": "string", "title": "Описание проблемы"},
	SRC: {
		"type": "enumeration",
		"title": "Источник обращения",
		"items": [{"ID": "71", "VALUE": "Звонок"}, {"ID": "72", "VALUE": "Портал"}],
	},
	CAT: {
		"type": "enumeration",
		"title": "Каталог услуг",
		"items": [{"ID": "51", "VALUE": "Выдача доступов"}, {"ID": "52", "VALUE": "Оборудование"}],
	},
	REQ: {"type": "employee", "title": "Кто обратился"},
	DUE: {"type": "date", "title": "Планируемый срок выполнения задачи"},
	DONE: {"type": "date", "title": "Дата выполнения"},
}
STAGES = [
	{"STATUS_ID": "DT1300_5:NEW", "NAME": "Новая", "SEMANTICS": None},
	{"STATUS_ID": "DT1300_5:WORK", "NAME": "В работе", "SEMANTICS": ""},
	{"STATUS_ID": "DT1300_5:VENDOR", "NAME": "У подрядчика", "SEMANTICS": ""},
	{"STATUS_ID": "DT1300_5:SUCCESS", "NAME": "Завершена", "SEMANTICS": "S"},
	{"STATUS_ID": "DT1300_5:FAIL", "NAME": "Отменена", "SEMANTICS": "F"},
]


def iso(dt):
	return dt.strftime("%Y-%m-%dT%H:%M:%S+03:00")


def item(
	item_id, stage, created_days_ago, requester=11, assigned=21, category="51", due=None, moved_days_ago=None
):
	now = now_datetime()
	created = now - timedelta(days=created_days_ago, minutes=1)
	moved = now - timedelta(days=moved_days_ago) if moved_days_ago is not None else created
	return {
		"id": item_id,
		"title": f"Заявка {item_id}",
		"stageId": f"DT1300_5:{stage}",
		"categoryId": 5,
		"createdTime": iso(created),
		"updatedTime": iso(moved),
		"movedTime": iso(moved),
		"assignedById": assigned,
		REQ: requester,
		CAT: category,
		SRC: "71",
		DUE: (now + timedelta(days=due)).strftime("%Y-%m-%dT03:00:00+03:00") if due is not None else "",
		DONE: "",
	}


def items():
	return [
		item(1, "NEW", 0),  # new today, no deadline
		item(2, "WORK", 3, due=-1),  # overdue
		item(3, "VENDOR", 10, due=2, category="52", assigned=0),  # without a responsible person
		item(4, "SUCCESS", 5, due=1, moved_days_ago=4),  # done in time
		item(5, "SUCCESS", 40, moved_days_ago=2),  # done, created before the period
		item(6, "FAIL", 1, moved_days_ago=0),  # cancelled
	]


def data(rows=None):
	return {
		"type": {"entityTypeId": ETID, "title": "Заявки технической поддержки"},
		"fields": FIELDS,
		"funnels": [{"id": 5, "name": "Общая", "stages": STAGES}],
		"items": items() if rows is None else rows,
	}


class FakeClient:
	def __init__(self):
		self.lists = []

	def crm_types(self):
		return [{"entityTypeId": ETID, "title": "Заявки технической поддержки"}]

	def result(self, method, params):
		if method == "crm.item.fields":
			return {"fields": FIELDS}
		if method == "crm.category.list":
			return {"categories": [{"id": 5, "name": "Общая"}]}
		raise AssertionError(method)

	def call_many(self, method, param_list):
		assert method == "crm.status.list"
		assert param_list[0]["filter"]["ENTITY_ID"] == "DYNAMIC_1300_STAGE_5"
		return [STAGES]

	def list_all(self, method, params, key=None):
		assert method == "crm.item.list" and key == "items"
		self.lists.append(params)
		wanted = set(params["filter"]["@stageId"])
		return [i for i in items() if i["stageId"] in wanted]


class TestHelpdesk(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in ("B24 Smart Item", "B24 Smart Process"):
			frappe.db.delete(doctype)
		frappe.db.delete("B24 User", {"portal": PORTAL})
		frappe.db.delete("B24 Portal", PORTAL)
		frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": PORTAL,
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
			}
		).insert()
		for b24_id, full_name in ((11, "Иванова Анна"), (21, "Петров Пётр")):
			frappe.get_doc(
				{
					"doctype": "B24 User",
					"uid": f"{PORTAL}:{b24_id}",
					"portal": PORTAL,
					"b24_id": b24_id,
					"full_name": full_name,
					"active": 1,
				}
			).insert(ignore_permissions=True)
		self.process = frappe.get_doc(
			{"doctype": "B24 Smart Process", "portal": PORTAL, "entity_type_id": ETID}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def sync(self, payload=None):
		log = smart_items.run_process_sync(
			self.process.name, commit=False, fetch=lambda _doc: payload or data()
		)
		return log

	def test_detect_fields(self):
		codes = smart_items.detect_fields(FIELDS)
		self.assertEqual(
			codes,
			{
				"requester_field": REQ,
				"source_field": SRC,
				"category_field": CAT,
				"deadline_field": DUE,
				"done_field": DONE,
			},
		)
		# a field set by hand is kept
		self.assertEqual(smart_items.detect_fields(FIELDS, {"category_field": SRC})["category_field"], SRC)

	def test_sync_mirror(self):
		log = self.sync()
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertEqual(self.process.name, f"{PORTAL}:{ETID}")
		doc = frappe.get_doc("B24 Smart Process", self.process.name)
		self.assertEqual(doc.title, "Заявки технической поддержки")
		self.assertEqual((doc.requester_field, doc.category_field, doc.deadline_field), (REQ, CAT, DUE))
		self.assertEqual([s.state for s in doc.stages], ["Открыта"] * 3 + ["Завершена", "Отменена"])
		self.assertEqual((doc.items_count, doc.open_count), (6, 3))

		two = frappe.get_doc("B24 Smart Item", f"{self.process.name}:2")
		self.assertEqual(
			(two.state, two.stage_name, two.category, two.source),
			("Открыта", "В работе", "Выдача доступов", "Звонок"),
		)
		self.assertEqual((two.requester_name, two.assigned_name), ("Иванова Анна", "Петров Пётр"))
		self.assertEqual(two.requester_b24, f"{PORTAL}:11")
		self.assertEqual(two.url, f"https://b24.example.local/crm/type/{ETID}/details/2/")
		self.assertIsNone(two.closed_at)
		self.assertIsNotNone(frappe.db.get_value("B24 Smart Item", f"{self.process.name}:4", "closed_at"))
		self.assertFalse(frappe.db.get_value("B24 Smart Item", f"{self.process.name}:3", "assigned_name"))

		# second load: one item gone, one moved to «done»
		rows = items()
		rows = [r for r in rows if r["id"] != 6]
		rows[0]["stageId"] = "DT1300_5:SUCCESS"
		stats = frappe.parse_json(self.sync(data(rows)).stats)
		self.assertEqual((stats["deleted"], stats["updated"], stats["created"]), (1, 1, 0))
		self.assertEqual(
			frappe.db.get_value("B24 Smart Item", f"{self.process.name}:1", "state"), "Завершена"
		)

	def test_same_item_twice(self):
		rows = items()
		closed = dict(rows[1], stageId="DT1300_5:SUCCESS")  # closed while the registry was reading
		log = self.sync(data(rows + [closed]))
		self.assertEqual(log.status, "Успех", log.messages)
		self.assertEqual(frappe.db.count("B24 Smart Item", {"smart_process": self.process.name}), 6)

	def test_guard(self):
		rows = [item(100 + n, "NEW", 1) for n in range(12)]
		self.sync(data(rows))
		log = self.sync(data([]))
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertEqual(frappe.db.count("B24 Smart Item"), 12)

	def test_fetch_reads_open_and_recent_closed(self):
		client = FakeClient()
		fetched = smart_items.fetch_process(self.process, client)
		self.assertEqual(len(fetched["items"]), 6)
		open_call, closed_call = client.lists
		self.assertEqual(
			open_call["filter"]["@stageId"], ["DT1300_5:NEW", "DT1300_5:WORK", "DT1300_5:VENDOR"]
		)
		self.assertEqual(closed_call["filter"]["@stageId"], ["DT1300_5:SUCCESS", "DT1300_5:FAIL"])
		self.assertIn(">=movedTime", closed_call["filter"])
		# only the state of the request: never the description of the problem
		self.assertIn(REQ, open_call["select"])
		self.assertNotIn("ufCrm9_0999", open_call["select"])

	def test_snapshot(self):
		self.sync()
		d = support.snapshot(days=30)
		k = d["kpis"]
		self.assertEqual((k["open"], k["overdue"], k["unassigned"], k["new_today"]), (3, 1, 1, 1))
		self.assertEqual((k["created"], k["done"], k["cancelled"]), (5, 2, 1))
		self.assertEqual((k["on_time"], k["with_deadline"]), (100, 1))
		self.assertEqual(
			[(s["stage"], s["value"]) for s in d["stages"]],
			[("Новая", 1), ("В работе", 1), ("У подрядчика", 1)],
		)
		self.assertEqual(d["open_items"][0]["item_id"], 2)  # overdue first
		self.assertTrue(d["open_items"][0]["overdue"])
		cats = {c["category"]: c for c in d["categories"]}
		self.assertEqual((cats["Выдача доступов"]["open"], cats["Выдача доступов"]["overdue"]), (2, 1))
		people = {p["name"]: p for p in d["people"]}
		self.assertEqual(people["Петров Пётр"]["open"], 2)
		self.assertEqual(people["Не назначен"]["open"], 1)
		self.assertEqual(len(d["trend"]), 30)
		self.assertEqual(d["trend"][-1]["open"], 3)
		self.assertEqual(sum(a["value"] for a in d["ages"]), 3)

	def test_section_access(self):
		self.sync()
		user = make_user("helpdesk-user@registry.test")
		frappe.db.delete("Registry Access Profile", {"profile_name": "Техподдержка"})
		api.save_profile({"profile_name": "Техподдержка", "sections": {"people": 1}, "members": [user]})
		frappe.set_user(user)
		self.assertRaises(frappe.PermissionError, api.support)
		frappe.set_user("Administrator")
		name = frappe.db.get_value("Registry Access Profile", {"profile_name": "Техподдержка"})
		api.save_profile(
			{"name": name, "profile_name": "Техподдержка", "sections": {"support": 1}, "members": [user]}
		)
		frappe.set_user(user)
		self.assertEqual(app_access.access()["sections"]["support"], 1)
		self.assertEqual(api.support(days=7)["kpis"]["open"], 3)
