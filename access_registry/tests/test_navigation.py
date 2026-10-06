"""Cards for cross-navigation: a position (by title), a department, an organization; search finds them."""

import frappe

from access_registry.registry import api
from access_registry.tests.test_it_assets import SnipeFixture
from access_registry.tests.test_permissions import make_user


class TestNavigation(SnipeFixture):
	def working_employment(self):
		return frappe.db.sql(
			"""select e.person, e.department, e.organization, pos.title as position
			from `tabEmployment` e join `tabHR Position` pos on pos.name = e.position
			where e.status = 'Работает' and ifnull(e.department, '') != '' limit 1""",
			as_dict=True,
		)[0]

	def test_position_department_organization(self):
		e = self.working_employment()
		pos = api.position(e.position)
		self.assertIn(e.person, {h.person for h in pos["holders"]})
		self.assertTrue(pos["departments"] and pos["organizations"])
		self.assertIsInstance(pos["access"], list)

		dep = api.department(e.department)
		self.assertIn(e.person, {h.person for h in dep["employees"]})
		self.assertGreaterEqual(dep["total"], len({h.person for h in dep["employees"]}))
		self.assertIn(e.position, {p["title"] for p in dep["positions"]})

		org = api.organization(e.organization)
		self.assertGreaterEqual(org["total"], 1)
		self.assertIn(e.department, {d["name"] for d in org["departments"]})

		# a role whose rule names the position shows on the position card
		role = frappe.get_doc(
			{
				"doctype": "Access Role",
				"role_name": "Тест навигации",
				"kind": "Должностная",
				"status": "Действует",
				"rules": [{"position_title": e.position}],
			}
		).insert(ignore_permissions=True)
		self.assertIn(role.name, {r.name for r in api.position(e.position)["roles"]})

		# the person card and the people list carry the ids for the links
		card = api.person(e.person)["person"]
		self.assertTrue(card["department_id"] and card["organization_id"])

	def test_search_and_rights(self):
		e = self.working_employment()
		found = {(r["kind"], r["id"]) for r in api.search(e.position[:5])}
		self.assertIn(("position", e.position), found)
		user = make_user("nav-nobody@registry.test")
		frappe.db.delete("Registry Access Profile", {"name": "Навигация: только техника"})
		api.save_profile(
			{"profile_name": "Навигация: только техника", "sections": {"equipment": 1}, "members": [user]}
		)
		frappe.set_user(user)
		self.assertRaises(frappe.PermissionError, api.position, e.position)
		self.assertRaises(frappe.PermissionError, api.department, e.department)
		self.assertRaises(frappe.PermissionError, api.organization, e.organization)
		self.assertRaises(
			frappe.DoesNotExistError,
			lambda: (frappe.set_user("Administrator"), api.position("Нет такой должности")),
		)
