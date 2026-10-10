"""Tests of access reviews: scope, reviewers, decisions and the app API."""

import frappe

from access_registry.access_roles import review as reviews
from access_registry.access_roles.reports import review_results
from access_registry.registry import api
from access_registry.tests.test_permissions import make_user
from access_registry.tests.test_roles import RoleFixture


class TestAccessReview(RoleFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("Access Review Item")
		frappe.db.delete("Access Review")
		self.fallback = make_user("auditor-review@registry.test", "Registry Auditor")
		# Иванов heads the accounting department: he reviews Петрова
		self.ivanov = make_user("ivanov@example.com", "Access Reviewer")
		self.petrova = make_user("petrova@example.com", "Access Reviewer")
		accounting = frappe.db.get_value("HR Department", {"title": "Бухгалтерия"}, "name")
		frappe.db.set_value("HR Department", accounting, "head", self.person(1))

	def create(self, **values):
		return frappe.get_doc(
			{"doctype": "Access Review", "title": "Пересмотр", "reviewer": self.fallback, **values}
		).insert()

	def items(self, review, **filters):
		return frappe.get_all(
			"Access Review Item",
			filters={"access_review": review, **filters},
			fields=["name", "person", "access_title", "system", "reviewer_user", "decision", "access_key"],
		)

	def test_manager_review(self):
		review = self.create()
		review.start()
		review.reload()
		self.assertEqual(review.status, "Идёт")
		items = self.items(review.name)
		self.assertEqual(len(items), 8)
		by_person = {}
		for item in items:
			by_person.setdefault(item.person, set()).add(item.reviewer_user)
		self.assertEqual(by_person[self.person(2)], {self.ivanov})  # manager from the HR data
		self.assertEqual(by_person[self.person(1)], {self.fallback})  # heads himself: nobody above
		self.assertEqual(by_person[self.person(8)], {self.fallback})  # dismissed people are reviewed too
		self.assertEqual(review.items_total, 8)
		self.assertEqual(review.items_unassigned, 0)

		frappe.set_user(self.ivanov)
		boot = api.bootstrap()
		self.assertFalse(boot["can"]["read"])
		self.assertEqual(boot["pending_reviews"], 4)
		self.assertRaises(frappe.PermissionError, api.dashboard)
		mine = api.my_reviews()
		self.assertEqual({i.full_name for i in mine}, {"Петрова Мария Сергеевна"})
		self.assertEqual(mine[0].department, "Бухгалтерия")
		kadr = next(i for i in mine if i.access_title == "1С: Кадровик")
		api.decide(kadr.name, "Отозвать", "Кадрами не занимается")
		self.assertEqual(api.decide_person(review.name, self.person(2), "Оставить"), 3)
		self.assertEqual(reviews.pending_count(), 0)
		other = self.items(review.name, reviewer_user=self.fallback)[0]
		self.assertRaises(frappe.PermissionError, api.decide, other.name, "Оставить")
		self.assertRaises(frappe.ValidationError, api.decide, kadr.name, "Может быть")

		frappe.set_user("Administrator")
		review.reload()
		self.assertEqual((review.items_done, review.items_revoke), (4, 1))
		result = api.review(review.name)
		self.assertEqual(len(result["items"]), 8)
		revoke = review_results({"access_review": review.name, "decision": "Отозвать"})[1]
		self.assertEqual(
			[(r.full_name, r.access_title, r.comment) for r in revoke],
			[("Петрова Мария Сергеевна", "1С: Кадровик", "Кадрами не занимается")],
		)
		self.assertEqual(len(review_results({"access_review": review.name, "decision": "Без решения"})[1]), 4)
		self.assertEqual(api.reviews()[0].items_revoke, 1)

		review.finish()
		frappe.set_user(self.fallback)
		self.assertRaises(frappe.ValidationError, api.decide, other.name, "Оставить")
		frappe.set_user("Administrator")
		review.reload()
		review.system = "1С"
		self.assertRaises(frappe.ValidationError, review.save)

	def test_nobody_decides_on_own_access(self):
		review = self.create()
		review.start()
		own = self.items(review.name, person=self.person(1))[0]  # Иванов's own access
		self.assertEqual(own.reviewer_user, self.fallback)
		frappe.db.set_value("Access Review Item", own.name, "reviewer_user", self.ivanov)
		frappe.set_user(self.ivanov)
		self.assertRaises(frappe.PermissionError, api.decide, own.name, "Оставить")

	def test_scope_and_owner_mode(self):
		ad_only = self.create(system="Active Directory")
		ad_only.start()
		self.assertEqual({i.system for i in self.items(ad_only.name)}, {"Active Directory"})
		self.assertEqual(len(self.items(ad_only.name)), 4)

		frappe.db.set_value("Entitlement", self.kadr, {"owner_person": self.person(2), "privileged": 1})
		owners = self.create(reviewer_mode="Владелец права", only_privileged=1)
		owners.start()
		items = self.items(owners.name)
		self.assertEqual({i.access_title for i in items}, {"1С: Кадровик"})
		# the owner reviews the holders, but not herself: her own access goes to the campaign reviewer
		reviewers = {i.person: i.reviewer_user for i in items}
		self.assertEqual(reviewers.pop(self.person(2)), self.fallback)
		self.assertEqual(set(reviewers.values()) - {self.petrova}, set())

		frappe.delete_doc("Entitlement", self.empty, force=True)
		raw = self.create(include_uncatalogued=1, system="1С")
		raw.start()
		uncatalogued = [i for i in self.items(raw.name) if i.access_key]
		self.assertEqual([i.access_title for i in uncatalogued], ["1С TST1: Пустой профиль"])

		single = self.create(reviewer_mode="Один проверяющий", reviewer=None)
		self.assertRaises(frappe.ValidationError, single.start)
		self.assertRaises(frappe.ValidationError, ad_only.start)  # already started
