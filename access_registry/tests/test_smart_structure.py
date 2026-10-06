"""The structure of Bitrix24 smart processes: description only, no item data."""

from frappe.tests.utils import FrappeTestCase

from access_registry.bitrix24.client import B24Error
from access_registry.bitrix24.smart_structure import describe


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
		assert params["select"] == ["id"]  # counts only, never the items
		return {"result": {"items": [{"id": 1}]}, "total": 42}


class TestSmartStructure(FrappeTestCase):
	def test_describe(self):
		data = describe(FakeClient())
		first, second = data["smart_processes"]
		self.assertEqual((first["title"], first["items"]), ("Заявки на доступ", 42))
		self.assertEqual([s["name"] for s in first["funnels"][0]["stages"]], ["Новая", "Выдан"])
		fields = {f["code"]: f for f in first["fields"]}
		self.assertEqual(fields["ufCrm5System"]["options"], ["1С", "AD"])
		self.assertTrue(fields["assignedById"]["system"] and fields["assignedById"]["required"])
		self.assertFalse(fields["ufCrm5System"]["system"])
		self.assertIn("ACCESS_DENIED", second["error"])
