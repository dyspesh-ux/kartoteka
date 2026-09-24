"""Tests of the ZUP mirror (stage 1). HTTP is replaced by synthetic fixtures."""

import copy
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from access_registry.sync.departments import ROOT_KEY, ensure_root, org_node_key
from access_registry.sync.engine import resolve_hierarchy, run_source_sync
from access_registry.sync.normalize import match_keys, normalize_name
from access_registry.sync.persons import merge_persons

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
TODAY = getdate("2026-06-01")
S1, S2 = "TST1", "TST2"
SYNC_USER = "sync-zup@access.local"

APP_DOCTYPES = [
	"HR Event",
	"HR Absence",
	"Person Merge Candidate",
	"Employment",
	"Person Source ID",
	"Person",
	"HR Position",
	"HR Department",
	"HR Organization",
	"Legal Entity",
	"Sync Log",
	"HR Source",
]


def g(kind, n):
	return f"00000000-0000-4000-{kind}-{n:012d}"


def ORG(n):
	return g("a000", n)


def DEP(n):
	return g("b000", n)


def EMP(n):
	return g("d000", n)


def FL(n):
	return g("e000", n)


def load(base: str) -> dict:
	data = {}
	for endpoint in ("meta", "organizations", "departments", "employees", "absences"):
		with open(os.path.join(FIXTURES, base, f"{endpoint}.json"), encoding="utf-8") as fh:
			data[endpoint] = json.load(fh)
	return data


def row(data, endpoint, guid, key):
	return next(r for r in data[endpoint] if r[key] == guid)


def person_of(source, person_guid):
	return frappe.db.get_value("Person Source ID", {"source": source, "person_guid": person_guid}, "parent")


class TestZupSync(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for doctype in APP_DOCTYPES:
			frappe.db.delete(doctype)
		frappe.db.delete("Version", {"ref_doctype": ["in", APP_DOCTYPES]})
		frappe.db.delete("Deleted Document", {"deleted_doctype": "Person"})
		for key in ("shrink_threshold_pct", "guard_min_records", "head_keywords", "sync_user"):
			frappe.db.set_single_value("Access Registry Settings", key, None)
		ensure_root()
		for code in (S1, S2):
			frappe.get_doc(
				{
					"doctype": "HR Source",
					"source_code": code,
					"title": code,
					"base_url": "http://127.0.0.1:9/hs",
				}
			).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ------------------------------------------------------------------ helpers

	def sync(self, source, data, today=TODAY):
		payload = copy.deepcopy(data)
		log = run_source_sync(
			source, today=today, commit=False, fetch=lambda *args, **kwargs: copy.deepcopy(payload)
		)
		self.assertEqual(frappe.session.user, "Administrator")
		return log

	def assertSuccess(self, log):
		self.assertEqual(log.status, "Успех", log.messages)

	def versions(self, doctype=None):
		filters = {"ref_doctype": ["in", APP_DOCTYPES]}
		if doctype:
			filters = {"ref_doctype": doctype}
		return frappe.db.count("Version", filters)

	def events(self, event_type=None):
		filters = {"event_type": event_type} if event_type else {}
		return frappe.get_all("HR Event", filters=filters, fields=["*"])

	def assertTreeConsistent(self):
		nodes = {
			n.name: n
			for n in frappe.get_all(
				"HR Department", fields=["name", "lft", "rgt", "parent_hr_department"], limit_page_length=0
			)
		}
		bounds = sorted([n.lft for n in nodes.values()] + [n.rgt for n in nodes.values()])
		self.assertEqual(bounds, list(range(1, 2 * len(nodes) + 1)), "lft/rgt must be a permutation 1..2n")
		roots = [n for n in nodes.values() if not n.parent_hr_department]
		self.assertEqual([r.name for r in roots], [ROOT_KEY])
		for node in nodes.values():
			self.assertLess(node.lft, node.rgt)
			if node.parent_hr_department:
				parent = nodes[node.parent_hr_department]
				self.assertTrue(parent.lft < node.lft and node.rgt < parent.rgt, node.name)
			descendants = [n for n in nodes.values() if node.lft < n.lft and n.rgt < node.rgt]
			self.assertEqual(node.rgt - node.lft + 1, 2 * (len(descendants) + 1), node.name)

	# ------------------------------------------------------------------ 1. first load

	def test_01_first_load(self):
		log = self.sync(S1, load("zup1"))
		self.assertSuccess(log)
		self.assertEqual(log.first_load, 1)

		self.assertEqual(
			sorted(frappe.get_all("Legal Entity", pluck="name")), ["7700000001-770001001", "7700000002"]
		)
		org1 = frappe.get_doc("HR Organization", f"{S1}:{ORG(1)}")
		org2 = frappe.get_doc("HR Organization", f"{S1}:{ORG(2)}")
		self.assertEqual(org1.legal_entity, "7700000001-770001001")
		self.assertFalse(org1.head_organization)
		self.assertEqual(org2.head_organization, org1.name)
		self.assertEqual(org2.legal_entity, "7700000002")

		root_children = frappe.get_all(
			"HR Department", filters={"parent_hr_department": ROOT_KEY}, pluck="name"
		)
		self.assertEqual(sorted(root_children), sorted([org_node_key(S1, ORG(1)), org_node_key(S1, ORG(2))]))
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(1)}", "parent_hr_department"),
			org_node_key(S1, ORG(1)),
		)
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(2)}", "parent_hr_department"), f"{S1}:{DEP(1)}"
		)
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(5)}", "parent_hr_department"), f"{S1}:{DEP(4)}"
		)
		self.assertEqual(frappe.db.count("HR Department", {"node_type": "Подразделение"}), 5)
		self.assertTreeConsistent()

		self.assertEqual(frappe.db.count("HR Position"), 7)
		self.assertEqual(frappe.db.count("Person"), 8)
		self.assertEqual(frappe.db.count("Employment"), 9)
		self.assertEqual(frappe.db.count("HR Absence"), 3)
		self.assertEqual(self.events(), [])

		person = frappe.get_doc("Person", person_of(S1, FL(1)))
		self.assertEqual(person.name, person.person_uuid)
		self.assertEqual(len(person.name), 36)
		self.assertEqual(person.full_name, "Иванов Иван Иванович")
		self.assertEqual(person.status, "Работает")
		self.assertEqual(person.presence, "На месте")
		self.assertEqual(str(person.birth_date), "1980-01-15")

		emp = frappe.get_doc("Employment", f"{S1}:{EMP(1)}")
		self.assertEqual(emp.title, "Иванов Иван Иванович · Генеральный директор")
		self.assertEqual(emp.status, "Работает")
		self.assertEqual(emp.modified_by, SYNC_USER)
		self.assertEqual(frappe.db.get_value("Employment", f"{S1}:{EMP(9)}", "status"), "Уволен")

		self.assertTrue(frappe.db.get_value("HR Source", S1, "last_sync"))
		self.assertIn("Успех", frappe.db.get_value("HR Source", S1, "last_status"))
		# /meta contains a state kind with category «Неизвестно»
		self.assertIn("Неизвестно", log.messages)
		self.assertTrue(json.loads(log.meta_snapshot)["ВидыСостояний"])
		stats = json.loads(log.stats)
		self.assertEqual(stats["Employment"]["created"], 9)
		# New departments are inserted straight under their final parent: no moves on the first load
		self.assertNotIn("moved", stats["HR Department"])

	# ------------------------------------------------------------------ 2. idempotency

	def test_02_repeat_sync_creates_no_versions(self):
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		before = self.versions()
		modified = frappe.db.get_value("Employment", f"{S1}:{EMP(1)}", "modified")

		log = self.sync(S1, data)
		self.assertSuccess(log)
		self.assertEqual(log.first_load, 0)
		self.assertEqual(self.versions(), before)
		self.assertEqual(frappe.db.get_value("Employment", f"{S1}:{EMP(1)}", "modified"), modified)
		stats = json.loads(log.stats)
		for entity in ("HR Organization", "HR Department", "HR Position", "Employment", "HR Absence"):
			self.assertEqual(set(stats[entity]), {"unchanged"}, entity)
		self.assertEqual(stats["Employment"]["unchanged"], 9)
		self.assertNotIn("status_updated", stats.get("Person", {}))
		self.assertEqual(self.events(), [])

	# ------------------------------------------------------------------ 3. transfer

	def test_03_transfer(self):
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		before = self.versions("Employment")
		emp = row(data, "employees", EMP(3), "СотрудникGUID")
		emp["ПодразделениеGUID"] = DEP(2)
		emp["Подразделение"] = "Бухгалтерия"
		self.assertSuccess(self.sync(S1, data))

		self.assertEqual(frappe.db.get_value("Employment", f"{S1}:{EMP(3)}", "department"), f"{S1}:{DEP(2)}")
		self.assertEqual(self.versions("Employment"), before + 1)
		events = self.events("Перевод")
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0].employment, f"{S1}:{EMP(3)}")
		self.assertEqual(events[0].person, person_of(S1, FL(3)))
		self.assertIn("Отдел продаж → Бухгалтерия", events[0].details)

	# ------------------------------------------------------------------ 4. termination

	def test_04_termination(self):
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		row(data, "employees", EMP(2), "СотрудникGUID")["ДатаУвольнения"] = "2026-06-30"
		self.assertSuccess(self.sync(S1, data))

		self.assertEqual(frappe.db.get_value("Employment", f"{S1}:{EMP(2)}", "status"), "Увольняется")
		person = person_of(S1, FL(2))
		self.assertEqual(frappe.db.get_value("Person", person, "status"), "Работает")
		upcoming = self.events("Предстоящее увольнение")
		self.assertEqual(len(upcoming), 1)
		self.assertEqual(str(upcoming[0].event_date), "2026-06-30")

		self.assertSuccess(self.sync(S1, data, today=getdate("2026-07-01")))
		self.assertEqual(frappe.db.get_value("Employment", f"{S1}:{EMP(2)}", "status"), "Уволен")
		self.assertEqual(frappe.db.get_value("Person", person, "status"), "Уволен")
		fired = self.events("Увольнение")
		self.assertEqual(len(fired), 1)
		self.assertEqual(fired[0].person, person)
		self.assertEqual(str(fired[0].event_date), "2026-06-30")
		self.assertEqual(len(self.events("Предстоящее увольнение")), 1)

	# ------------------------------------------------------------------ 5. not hired

	def test_05_card_without_hire_date(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		emp = frappe.get_doc("Employment", f"{S1}:{EMP(8)}")
		self.assertEqual(emp.zup_state, "Уволен")
		self.assertEqual(emp.status, "Не принят")
		self.assertEqual(frappe.db.get_value("Person", emp.person, "status"), "Не принят")

	# ------------------------------------------------------------------ 6. internal part-time

	def test_06_two_employments_one_person(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		person = person_of(S1, FL(4))
		self.assertEqual(frappe.db.count("Person Source ID", {"parent": person}), 1)
		self.assertEqual(
			sorted(frappe.get_all("Employment", filters={"person": person}, pluck="name")),
			sorted([f"{S1}:{EMP(4)}", f"{S1}:{EMP(5)}"]),
		)
		self.assertEqual(frappe.db.get_value("Person", person, "external_part_time_only"), 0)

	# ------------------------------------------------------------------ 7. external part-time

	def test_07_external_part_time_only(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertEqual(frappe.db.get_value("Person", person_of(S1, FL(5)), "external_part_time_only"), 1)
		self.assertEqual(frappe.db.get_value("Person", person_of(S1, FL(1)), "external_part_time_only"), 0)

	# ------------------------------------------------------------------ 8. same person in two bases

	def test_08_person_in_two_bases(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertSuccess(self.sync(S2, load("zup2")))

		person = person_of(S1, FL(1))
		self.assertEqual(person_of(S2, FL(9)), person)
		ids = frappe.get_all("Person Source ID", filters={"parent": person}, fields=["source", "person_guid"])
		self.assertEqual(sorted((r.source, r.person_guid) for r in ids), [(S1, FL(1)), (S2, FL(9))])
		self.assertEqual(frappe.db.count("Employment", {"person": person}), 2)
		# The same legal entity from both bases; its title is not overwritten by the second base
		self.assertEqual(frappe.db.count("Legal Entity", {"inn": "7700000001"}), 1)
		self.assertEqual(frappe.db.get_value("Legal Entity", "7700000001-770001001", "title"), "Ромашка ООО")
		self.assertEqual(
			frappe.db.get_value("HR Organization", f"{S2}:{ORG(3)}", "legal_entity"), "7700000001-770001001"
		)
		# First load of the second source: no events at all
		self.assertEqual(self.events(), [])

	# ------------------------------------------------------------------ 9. surname change and merge

	def test_09_surname_change_and_merge(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertSuccess(self.sync(S2, load("zup2")))

		person_a = person_of(S1, FL(2))
		person_b = person_of(S2, FL(11))
		self.assertNotEqual(person_a, person_b)
		candidate = frappe.get_doc("Person Merge Candidate", {"person_b": person_b})
		self.assertEqual(candidate.person_a, person_a)
		self.assertEqual(candidate.reason, "Совпадают имя, отчество и дата рождения (смена фамилии?)")
		self.assertEqual(candidate.status, "Открыт")

		# Links to B from other DocTypes must follow the merge
		dept = frappe.get_doc("HR Department", f"{S1}:{DEP(3)}")
		dept.manual_head = person_b
		dept.save()
		frappe.db.set_value("Legal Entity", "7700000002", "director", person_b)
		emp_b = f"{S2}:{EMP(12)}"

		candidate.merge()

		self.assertFalse(frappe.db.exists("Person", person_b))
		a = frappe.get_doc("Person", person_a)
		self.assertEqual(a.person_uuid, person_a)
		self.assertEqual(a.last_name, "Петрова")
		self.assertEqual(sorted((r.source, r.person_guid) for r in a.source_ids), [(S1, FL(2)), (S2, FL(11))])
		self.assertEqual(frappe.db.get_value("Employment", emp_b, "person"), person_a)
		self.assertEqual(frappe.db.get_value("HR Department", f"{S1}:{DEP(3)}", "manual_head"), person_a)
		self.assertEqual(frappe.db.get_value("HR Department", f"{S1}:{DEP(3)}", "head"), person_a)
		self.assertEqual(frappe.db.get_value("Legal Entity", "7700000002", "director"), person_a)
		self.assertEqual(frappe.db.get_value("Person Merge Candidate", candidate.name, "status"), "Склеено")
		self.assertEqual(
			frappe.db.get_value("Person Merge Candidate", candidate.name, "merged_uuid"), person_b
		)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Person", "reference_name": person_a, "content": ["like", "%Склеен%"]},
			)
		)
		# Syncing both sources again keeps everything attached to A and creates nothing new
		self.assertSuccess(self.sync(S2, load("zup2")))
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertEqual(person_of(S2, FL(11)), person_a)
		self.assertFalse(frappe.db.exists("Person", person_b))
		self.assertEqual(frappe.db.count("Person Merge Candidate", {"status": "Открыт"}), 0)

	def test_09b_different_people_not_suggested_again(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertSuccess(self.sync(S2, load("zup2")))
		candidate = frappe.get_doc("Person Merge Candidate", {"person_b": person_of(S2, FL(11))})
		candidate.mark_different()
		# A new physical person record with the same data appears in the second base
		data = load("zup2")
		clone = copy.deepcopy(row(data, "employees", EMP(12), "СотрудникGUID"))
		clone["СотрудникGUID"] = EMP(13)
		clone["ФизЛицоGUID"] = FL(12)
		data["employees"].append(clone)
		self.assertSuccess(self.sync(S2, data))
		self.assertEqual(
			frappe.db.get_value("Person Merge Candidate", candidate.name, "status"), "Разные люди"
		)
		pair = sorted([candidate.person_a, candidate.person_b])
		self.assertEqual(frappe.db.count("Person Merge Candidate", {"pair_key": "|".join(pair)}), 1)
		# The duplicate physical person in the same base is suggested against the existing Волкова
		self.assertTrue(
			frappe.db.exists(
				"Person Merge Candidate",
				{"person_b": person_of(S2, FL(12)), "reason": "Совпадают ФИО и дата рождения"},
			)
		)

	# ------------------------------------------------------------------ 10. guard

	def test_10_guard_stops_on_shrink(self):
		frappe.db.set_single_value("Access Registry Settings", "guard_min_records", 4)
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		versions, events = self.versions(), len(self.events())
		snapshot = frappe.get_all(
			"Employment", fields=["name", "missing", "status", "modified"], order_by="name"
		)

		data["employees"] = data["employees"][:4]
		data["departments"][0]["Наименование"] = "Дирекция (переименована)"
		log = self.sync(S1, data)

		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertIn("Допустимое сокращение выгрузки", log.messages)
		self.assertIn("сотрудников", log.messages)
		self.assertEqual(
			frappe.get_all("Employment", fields=["name", "missing", "status", "modified"], order_by="name"),
			snapshot,
		)
		self.assertEqual(frappe.db.get_value("HR Department", f"{S1}:{DEP(1)}", "title"), "Дирекция")
		self.assertEqual(self.versions(), versions)
		self.assertEqual(len(self.events()), events)
		self.assertIn("Остановлен предохранителем", frappe.db.get_value("HR Source", S1, "last_status"))

		# Raising the threshold lets the same export through
		frappe.db.set_single_value("Access Registry Settings", "shrink_threshold_pct", 60)
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.count("Employment", {"source": S1, "missing": 1}), 5)

	# ------------------------------------------------------------------ 11. missing from export

	def test_11_missing_and_back(self):
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		removed = copy.deepcopy(data)
		removed["employees"] = [r for r in removed["employees"] if r["СотрудникGUID"] != EMP(3)]
		self.assertSuccess(self.sync(S1, removed))

		emp = frappe.get_doc("Employment", f"{S1}:{EMP(3)}")
		self.assertEqual(emp.missing, 1)
		self.assertEqual(emp.status, "Нет в выгрузке")
		self.assertEqual(frappe.db.get_value("Person", emp.person, "status"), "Нет в выгрузке")
		gone = self.events("Пропал из выгрузки")
		self.assertEqual([e.employment for e in gone], [emp.name])

		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("Employment", emp.name, "missing"), 0)
		self.assertEqual(frappe.db.get_value("Employment", emp.name, "status"), "Работает")
		self.assertEqual(len(self.events("Вернулся в выгрузку")), 1)
		self.assertEqual(len(self.events("Приём")), 1)

	# ------------------------------------------------------------------ 12. department tree

	def test_12_department_tree(self):
		data = load("zup1")
		orphan = copy.deepcopy(data["departments"][1])
		orphan.update(
			{
				"ПодразделениеGUID": DEP(7),
				"Код": "RMЗП-0007",
				"Наименование": "Архив",
				"РодительGUID": DEP(99),
			}
		)
		data["departments"].append(orphan)
		log = self.sync(S1, data)
		self.assertSuccess(log)
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(7)}", "parent_hr_department"),
			org_node_key(S1, ORG(1)),
		)
		self.assertIn(DEP(99), log.messages)
		self.assertTreeConsistent()

		# Move «Отдел продаж» under «Бухгалтерия»
		row(data, "departments", DEP(3), "ПодразделениеGUID")["РодительGUID"] = DEP(2)
		# Swap parent/child: «Смена А» becomes the parent of «Склад»
		row(data, "departments", DEP(5), "ПодразделениеGUID")["РодительGUID"] = None
		row(data, "departments", DEP(4), "ПодразделениеGUID")["РодительGUID"] = DEP(5)
		versions = self.versions("HR Department")
		log = self.sync(S1, data)
		self.assertSuccess(log)
		self.assertEqual(json.loads(log.stats)["HR Department"]["moved"], 3)
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(3)}", "parent_hr_department"), f"{S1}:{DEP(2)}"
		)
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(4)}", "parent_hr_department"), f"{S1}:{DEP(5)}"
		)
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(5)}", "parent_hr_department"),
			org_node_key(S1, ORG(2)),
		)
		dept2 = frappe.db.get_value("HR Department", f"{S1}:{DEP(2)}", ["lft", "rgt"], as_dict=True)
		dept3 = frappe.db.get_value("HR Department", f"{S1}:{DEP(3)}", ["lft", "rgt"], as_dict=True)
		self.assertTrue(dept2.lft < dept3.lft < dept3.rgt < dept2.rgt)
		self.assertTreeConsistent()
		self.assertEqual(self.versions("HR Department"), versions + 3)

		# The department disappears from the catalogue: flagged, never deleted
		data["departments"] = [r for r in data["departments"] if r["ПодразделениеGUID"] != DEP(7)]
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("HR Department", f"{S1}:{DEP(7)}", "missing"), 1)

	def test_12b_hierarchy_cycles(self):
		desired = {"a": "b", "b": "c", "c": "a", "d": "a", "e": None, "f": "e"}
		depth, cycles = resolve_hierarchy(desired)
		self.assertEqual(len(cycles), 1)
		self.assertEqual(sorted(cycles[0]), ["a", "b", "c"])
		self.assertEqual(depth["d"], 2)
		self.assertEqual(depth["f"], 2)
		self.assertIsNone(desired["a"])

	# ------------------------------------------------------------------ 13. heads

	def test_13_department_heads(self):
		data = load("zup1")
		row(data, "departments", DEP(5), "ПодразделениеGUID")["РуководительФизЛицоGUID"] = FL(999)
		log = self.sync(S1, data)
		self.assertSuccess(log)
		self.assertIn(FL(999), log.messages)

		dir_ = frappe.get_doc("HR Department", f"{S1}:{DEP(1)}")
		self.assertEqual(dir_.zup_head, person_of(S1, FL(1)))
		self.assertEqual(dir_.head, person_of(S1, FL(1)))
		self.assertEqual(dir_.head_source, "ЗУП")
		self.assertFalse(dir_.head_candidate)

		# «Начальник отдела продаж» (main job) wins over «Менеджер»
		sales = frappe.get_doc("HR Department", f"{S1}:{DEP(3)}")
		self.assertEqual(sales.head_candidate, person_of(S1, FL(4)))
		self.assertFalse(sales.head)
		# «Главный бухгалтер» matches «главный»; «Бухгалтер» (part-time) does not match anything
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(2)}", "head_candidate"), person_of(S1, FL(2))
		)
		# «Заведующий складом»
		self.assertEqual(
			frappe.db.get_value("HR Department", f"{S1}:{DEP(4)}", "head_candidate"), person_of(S1, FL(5))
		)

		# Accepting the candidate (what the button does): manual head, candidate cleared on next sync
		sales.manual_head = sales.head_candidate
		sales.save()
		self.assertEqual(sales.head_source, "Вручную")
		self.assertSuccess(self.sync(S1, data))
		sales.reload()
		self.assertFalse(sales.head_candidate)
		self.assertEqual(sales.head, person_of(S1, FL(4)))

		# Manual and ZUP heads differ → conflict, ZUP wins
		dir_.reload()
		dir_.manual_head = person_of(S1, FL(3))
		dir_.save()
		self.assertEqual(dir_.head_conflict, 1)
		self.assertEqual(dir_.head, person_of(S1, FL(1)))
		self.assertEqual(dir_.head_source, "ЗУП")

		# ZUP head removed → manual head takes over, no conflict
		row(data, "departments", DEP(1), "ПодразделениеGUID")["РуководительФизЛицоGUID"] = None
		self.assertSuccess(self.sync(S1, data))
		dir_.reload()
		self.assertFalse(dir_.zup_head)
		self.assertEqual(dir_.head, person_of(S1, FL(3)))
		self.assertEqual(dir_.head_source, "Вручную")
		self.assertEqual(dir_.head_conflict, 0)

	# ------------------------------------------------------------------ 14. parental leave

	def test_14_parental_leave_presence(self):
		data = load("zup1")
		emp = row(data, "employees", EMP(7), "СотрудникGUID")
		working = dict(emp)
		emp.update(
			{
				"Категория": "Работает",
				"КадровоеСостояниеКод": "Работа",
				"КадровоеСостояние": "Работа",
				"ОтсутствуетС": None,
				"ОтсутствуетПо": None,
				"ФактическиРаботает": True,
			}
		)
		self.assertSuccess(self.sync(S1, data))
		person = person_of(S1, FL(6))
		self.assertEqual(frappe.db.get_value("Person", person, "presence"), "На месте")

		emp.update(working)  # parental leave, not working
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("Person", person, "presence"), "Длительное отсутствие")
		self.assertEqual([e.person for e in self.events("Уход в отпуск по уходу")], [person])

		emp.update({"ДоляНеполногоВремени": 0.5, "ФактическиРаботает": True})  # works part-time during leave
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("Person", person, "presence"), "На месте")
		self.assertEqual(frappe.db.get_value("Employment", f"{S1}:{EMP(7)}", "part_time_share"), 0.5)
		self.assertEqual([e.person for e in self.events("Выход из отпуска по уходу")], [person])

		# Ordinary vacation or sick leave never changes presence
		emp2 = row(data, "employees", EMP(2), "СотрудникGUID")
		emp2.update({"Категория": "Больничный", "ФактическиРаботает": False})
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("Person", person_of(S1, FL(2)), "presence"), "На месте")

	def test_14b_first_load_parental_leave_without_event(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertEqual(
			frappe.db.get_value("Person", person_of(S1, FL(6)), "presence"), "Длительное отсутствие"
		)
		self.assertEqual(self.events(), [])

	# ------------------------------------------------------------------ 15. cancelled absence

	def test_15_cancelled_absence(self):
		data = load("zup1")
		old = copy.deepcopy(data["absences"][1])
		old.update({"ДатаНачала": "2025-01-10", "ДатаОкончания": "2025-01-20"})  # outside the window
		data["absences"].append(old)
		self.assertSuccess(self.sync(S1, data))
		vacation = f"{S1}:{EMP(3)}:ОтпускОсновной:2026-06-10"
		outside = f"{S1}:{EMP(3)}:ОтпускОсновной:2025-01-10"
		absence = frappe.get_doc("HR Absence", vacation)
		self.assertEqual(absence.employment, f"{S1}:{EMP(3)}")
		self.assertEqual(absence.person, person_of(S1, FL(3)))
		self.assertEqual(absence.org_source, "КадровыеДанные")

		# The end date is extended: same record
		data["absences"][1]["ДатаОкончания"] = "2026-06-30"
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(str(frappe.db.get_value("HR Absence", vacation, "date_to")), "2026-06-30")
		self.assertEqual(frappe.db.count("HR Absence", {"employment": f"{S1}:{EMP(3)}"}), 2)

		data["absences"] = [
			r for r in data["absences"] if r["ДатаНачала"] not in ("2026-06-10", "2025-01-10")
		]
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("HR Absence", vacation, "cancelled"), 1)
		self.assertEqual(frappe.db.get_value("HR Absence", outside, "cancelled"), 0)
		self.assertEqual(frappe.db.count("HR Absence", {"cancelled": 1}), 1)

		# Restored in the source → no longer cancelled
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		self.assertEqual(frappe.db.get_value("HR Absence", vacation, "cancelled"), 0)

	# ------------------------------------------------------------------ 16. Latin look-alikes

	def test_16_latin_letters_in_surname(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		self.assertSuccess(self.sync(S2, load("zup2")))
		person = person_of(S1, FL(5))
		self.assertEqual(person_of(S2, FL(10)), person)
		# Now there is a main job in the second base
		self.assertEqual(frappe.db.get_value("Person", person, "external_part_time_only"), 0)
		self.assertEqual(normalize_name("  Aндрeев  Oлег "), normalize_name("Андреев олег"))
		self.assertEqual(normalize_name("Фёдоров"), "федоров")
		self.assertEqual(match_keys("Иванов", "", "", "1980-01-01"), ("", ""))

	# ------------------------------------------------------------------ errors and misc

	def test_error_is_logged_with_traceback_and_rolled_back(self):
		data = load("zup1")
		self.assertSuccess(self.sync(S1, data))
		versions = self.versions()
		data["departments"][0]["Наименование"] = "Новое имя"

		def broken(*args, **kwargs):
			payload = copy.deepcopy(data)
			payload["employees"][0]["ДатаПриема"] = "не дата"
			return payload

		log = run_source_sync(S1, today=TODAY, commit=False, fetch=broken)
		self.assertEqual(log.status, "Ошибка")
		self.assertIn("Traceback", log.messages)
		self.assertEqual(frappe.db.get_value("HR Department", f"{S1}:{DEP(1)}", "title"), "Дирекция")
		self.assertEqual(self.versions(), versions)
		self.assertIn("Ошибка", frappe.db.get_value("HR Source", S1, "last_status"))
		self.assertEqual(frappe.session.user, "Administrator")

	def test_settings_defaults_without_saved_single(self):
		from access_registry.settings import get_settings

		frappe.db.delete("Singles", {"doctype": "Access Registry Settings"})
		settings = get_settings()
		self.assertEqual(settings.shrink_threshold_pct, 10)
		self.assertEqual(settings.guard_min_records, 20)
		self.assertEqual(settings.sync_user, SYNC_USER)
		self.assertEqual(settings.absence_days_back, 30)
		self.assertEqual(settings.absence_days_ahead, 180)
		self.assertEqual(settings.head_keywords_list[0], "руководитель")
		self.assertEqual(len(settings.head_keywords_list), 6)

	def test_birth_date_is_permlevel_1(self):
		meta = frappe.get_meta("Person")
		self.assertEqual(meta.get_field("birth_date").permlevel, 1)
		self.assertEqual(meta.get_field("match_key").permlevel, 1)
		self.assertTrue(
			any(p.permlevel == 1 and p.role == "System Manager" and p.read for p in meta.permissions)
		)

	def test_merge_keeps_uuid_and_rejects_self(self):
		self.assertSuccess(self.sync(S1, load("zup1")))
		a = person_of(S1, FL(1))
		self.assertRaises(frappe.ValidationError, merge_persons, a, a)

	def test_scheduler_day_and_night_jobs(self):
		cron = frappe.get_hooks("scheduler_events")["cron"]
		methods = [m for jobs in cron.values() for m in jobs]
		self.assertIn("access_registry.sync.engine.scheduled_sync_day", cron["*/30 7-20 * * *"])
		self.assertIn("access_registry.sync.engine.scheduled_sync_night", cron["0 21-23,0-6 * * *"])
		# Scheduled Job Type is keyed by method: the same method twice would lose one schedule
		self.assertEqual(len(methods), len(set(methods)))
