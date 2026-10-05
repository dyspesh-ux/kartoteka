"""Treasury rights of BIT.Finance from the 1C snapshot: import, history, role model, report."""

import copy
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.access_catalog import bit_report, importer
from access_registry.access_roles import engine
from access_registry.tests import test_catalog
from access_registry.tests.test_catalog import FIXTURES, S1, USR, snapshot


def bit_section() -> dict:
	with open(os.path.join(FIXTURES, "bit_sample.json"), encoding="utf-8") as fh:
		return json.load(fh)


def with_bit(bit=None) -> dict:
	data = snapshot()
	data["bit"] = copy.deepcopy(bit or bit_section())
	return data


class TestBitRights(FrappeTestCase):
	# the same base, HR and catalog setup as the 1C catalog tests
	setUp = test_catalog.TestAccessCatalog.setUp
	tearDown = test_catalog.TestAccessCatalog.tearDown
	hr_sync = test_catalog.TestAccessCatalog.hr_sync

	def imp(self, data):
		result, _warnings = importer.import_snapshot_data(S1, copy.deepcopy(data))
		return result

	def rights(self, n):
		return frappe.get_doc("IB User", f"{S1}:{USR(n)}").bit_rights

	def test_import_and_history(self):
		result = self.imp(with_bit())
		self.assertEqual(result["bit_rights"], 10)
		ivanov = self.rights(1)
		visas = [r for r in ivanov if r.kind == "Виза"]
		# the same visa with two conditions is two rights
		self.assertEqual(sorted(r.condition for r in visas), ["Руководитель Дирекции", "Руководитель Склада"])
		role = next(r for r in ivanov if r.kind == "Роль исполнителя")
		self.assertEqual(
			(role.right_name, role.object, role.cfo), ("Руководитель ЦФО", "Дирекция", "Дирекция")
		)
		petrova = {(r.kind, r.right_name): r for r in self.rights(2)}
		self.assertEqual(petrova[("Доступ к ЦФО", "Отдел продаж")].access, "чтение")
		self.assertEqual(petrova[("Доступ к ЦФО", "Склад")].object, "статья: Аренда офиса")
		sidorov = next(r for r in self.rights(4) if r.kind == "Доступ к ЦФО")
		self.assertEqual(sidorov.deputy_for, "Иванов Иван Иванович")

		# nothing changed — nothing saved
		self.assertEqual(self.imp(with_bit())["changed"], 0)
		# a visa taken away: the row goes, the version keeps it
		bit = bit_section()
		bit["visa_rights"] = [v for v in bit["visa_rights"] if v["visa_name"] != "Казначей"]
		self.imp(with_bit(bit))
		self.assertFalse([r for r in self.rights(2) if r.kind == "Виза"])
		versions = frappe.get_all(
			"Version", filters={"ref_doctype": "IB User", "docname": f"{S1}:{USR(2)}"}, pluck="data"
		)
		self.assertTrue(any("b100-000000000002" in v for v in versions), versions)
		# a snapshot without the section keeps the rights
		self.imp(snapshot())
		self.assertEqual(len(self.rights(1)), 4)

	def test_role_model_report_and_card(self):
		self.hr_sync()
		self.imp(with_bit())
		person = frappe.db.get_value("IB User", f"{S1}:{USR(1)}", "person")
		self.assertTrue(person)
		keys = engine.raw_accesses([person])[person]
		key = engine.bit_key(S1, "Виза", "Руководитель ЦФО")
		self.assertIn(key, keys)
		self.assertIn(engine.bit_key(S1, "Доступ к ЦФО", "Дирекция"), keys)
		# into the catalog: the entitlement covers the visa in this base
		entitlement = engine.ensure_entitlement(key)
		doc = frappe.get_doc("Entitlement", entitlement)
		self.assertEqual((doc.system, doc.bit_kind, doc.bit_name), ("1С", "Виза", "Руководитель ЦФО"))
		self.assertIn("БИТ.Финанс", doc.title)
		self.assertIn(key, engine.entitlement_keys()[entitlement])
		actual, _other = engine.actual_entitlements([person])
		self.assertIn(entitlement, actual[person])

		_columns, rows = bit_report.bit_rights({"kind": "Виза"})
		self.assertTrue(rows)
		self.assertEqual({r["kind"] for r in rows}, {"Виза"})
		_columns, rows = bit_report.bit_rights({"cfo": "Склад"})
		self.assertEqual({r["right_name"] for r in rows}, {"Склад"})

		from access_registry.registry import api

		card = api.person(person)
		account = next(a for a in card["ib"] if a["name"] == f"{S1}:{USR(1)}")
		self.assertEqual(len(account["bit"]), 4)
