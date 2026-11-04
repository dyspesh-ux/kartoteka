"""The structure of Bitrix24 smart processes: description only, no item data."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.bitrix24.client import B24Error
from access_registry.bitrix24.smart_structure import _field, build, describe, start
from access_registry.tests.test_permissions import make_user


class FakeClient:
	def crm_types(self):
		return [
			{
				"entityTypeId": 1040,
				"title": "Заявки на доступ",
				"isCategoriesEnabled": "Y",
				"isStagesEnabled": "Y",
			},
			{"entityTypeId": 1050, "title": "Без прав", "isCategoriesEnabled": "N"},
		]

	def result(self, method, params):
		if params.get("entityTypeId") == 1050:
			raise B24Error("ACCESS_DENIED")
		if method == "crm.category.list":
			return {"categories": [{"id": 7, "name": "Основная", "isDefault": "Y"}]}
		if method == "crm.status.list":
			assert params["filter"]["ENTITY_ID"] == "DYNAMIC_1040_STAGE_7"
			return [
				{"STATUS_ID": "DT1040_7:NEW", "NAME": "Новая", "SEMANTICS": None},
				{"STATUS_ID": "DT1040_7:SUCCESS", "NAME": "Выдан", "SEMANTICS": "S"},
			]
		if method == "crm.item.fields":
			return {
				"fields": {
					"title": {"type": "string", "title": "Название"},
					"assignedById": {"type": "user", "title": "Ответственный", "isRequired": True},
					"ufCrm5System": {
						"type": "enumeration",
						"title": "Система",
						"items": [{"ID": "1", "VALUE": "1С"}, {"ID": "2", "VALUE": "AD"}],
					},
				}
			}
		raise AssertionError(method)

	def call(self, method, params):
		assert method == "crm.item.list" and params["select"] == ["id"]  # counts only, never the items
		return {"result": {"items": [{"id": 1}]}, "total": 42}

	def batch(self, commands, halt=False, totals=None):
		self.batches = getattr(self, "batches", 0) + 1
		results = {}
		for key, (method, params) in commands.items():
			try:
				if method == "crm.item.list":
					answer = self.call(method, params)
					results[key] = answer["result"]
					if totals is not None:
						totals[key] = answer["total"]
				else:
					results[key] = self.result(method, params)
			except B24Error as e:
				results[key] = e
		return results


class TestSmartStructure(FrappeTestCase):
	def test_describe(self):
		client = FakeClient()
		data = describe(client)
		self.assertEqual(client.batches, 2)  # whole portal in two batches, no call per funnel
		first, second = data["smart_processes"]
		self.assertEqual((first["title"], first["items"]), ("Заявки на доступ", 42))
		self.assertEqual([s["name"] for s in first["funnels"][0]["stages"]], ["Новая", "Выдан"])
		fields = {f["code"]: f for f in first["fields"]}
		self.assertEqual(fields["ufCrm5System"]["options"], ["1С", "AD"])
		self.assertTrue(fields["assignedById"]["system"] and fields["assignedById"]["required"])
		self.assertFalse(fields["ufCrm5System"]["system"])
		self.assertIn("ACCESS_DENIED", second["error"])

	def test_build_attaches_file(self):
		portal = frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": "smart-test",
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
			}
		).insert()
		try:
			with (
				patch("access_registry.bitrix24.sync.make_client", return_value=FakeClient()),
				patch.object(frappe.db, "commit"),
			):
				name = build(portal.name)
			file = frappe.get_doc("File", name)
			self.assertEqual((file.attached_to_doctype, file.attached_to_name), ("B24 Portal", portal.name))
			self.assertTrue(file.is_private)
			data = json.loads(file.get_content())
			self.assertEqual(len(data["smart_processes"]), 2)
		finally:
			frappe.db.rollback()


class TestFieldDescription(FrappeTestCase):
	def test_shapes(self):
		f = _field(
			"ufX",
			{
				"formLabel": "Система",  # no «title»: the form label is used
				"type": "enumeration",
				"isMultiple": "Y",
				"items": [{"ID": "1", "VALUE": "1С"}, {"ID": "2", "VALUE": "AD"}, "broken"],
			},
		)
		self.assertEqual(
			(f["title"], f["type"], f["multiple"], f["required"], f["system"], f["options"]),
			("Система", "enumeration", True, False, False, ["1С", "AD"]),
		)
		f = _field("title", {"type": "string"})  # a system field without any label
		self.assertTrue(f["system"])
		self.assertEqual(f["title"], "title")
		self.assertNotIn("options", f)
		f = _field("ufPos", {"type": "crm", "settings": {"DYNAMIC_1086": "Y", "DYNAMIC_1116": "N"}})
		self.assertEqual(f["links_to"], ["DYNAMIC_1086"])  # only the links switched on


class EmptyPortalClient:
	def crm_types(self):
		return []

	def batch(self, *args, **kwargs):
		raise AssertionError("nothing to ask about on an empty portal")


class MessyClient:
	"""Categories come as a bare list; the stages of the funnel cannot be read."""

	def crm_types(self):
		return [{"entityTypeId": 1060, "title": "Заявки на доступ", "isCategoriesEnabled": "Y"}]

	def batch(self, commands, halt=False, totals=None):
		results = {}
		for key, (method, _params) in commands.items():
			if method == "crm.category.list":
				results[key] = [{"id": 3, "name": "Основная"}]  # a list, not {"categories": [...]}
			elif method == "crm.item.fields":
				results[key] = {"fields": {"title": {"type": "string", "title": "Название"}}}
			elif method == "crm.item.list":
				results[key] = {"items": []}
				if totals is not None:
					totals[key] = 0
			elif method == "crm.status.list":
				results[key] = B24Error("ACCESS_DENIED", "Доступ запрещён", method)
		return results


class TestDescribeEdges(FrappeTestCase):
	def test_portal_without_processes(self):
		self.assertEqual(describe(EmptyPortalClient())["smart_processes"], [])

	def test_list_categories_and_unreadable_stages(self):
		data = describe(MessyClient())
		(process,) = data["smart_processes"]
		self.assertEqual(process["items"], 0)
		self.assertEqual(process["funnels"][0]["name"], "Основная")
		self.assertEqual(process["funnels"][0]["stages"], [])  # the error does not break the funnel
		self.assertIn("ACCESS_DENIED", process["error"])


class TestStartAndBuild(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("B24 Portal", "smart-edges")
		self.portal = frappe.get_doc(
			{
				"doctype": "B24 Portal",
				"portal_code": "smart-edges",
				"title": "Портал",
				"webhook": "https://b24.example.local/rest/1/secret/",
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_start_enqueues_and_checks_rights(self):
		with patch("frappe.enqueue") as enqueue:
			message = start(self.portal.name)
		self.assertIn("Структура смарт-процессов", message)
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["job_id"], f"b24_smart_structure::{self.portal.name}")
		self.assertTrue(kwargs["deduplicate"])
		user = make_user("smart-structure@registry.test")
		frappe.set_user(user)
		frappe.flags.in_test = False  # frappe.only_for() is a no-op under the test runner
		try:
			with patch("frappe.enqueue"):
				self.assertRaises(frappe.PermissionError, start, self.portal.name)
		finally:
			frappe.flags.in_test = True

	def test_build_failure_is_reported_to_the_user(self):
		with (
			patch(
				"access_registry.bitrix24.sync.make_client",
				side_effect=B24Error("NO_AUTH", "Вебхук не работает", "batch"),
			),
			patch("frappe.publish_realtime") as publish,
		):
			self.assertIsNone(build(self.portal.name, user="Administrator"))
		(_event, payload), kwargs = publish.call_args
		self.assertEqual(payload["portal"], self.portal.name)
		self.assertIn("NO_AUTH", payload["error"])
		self.assertEqual(kwargs["user"], "Administrator")
