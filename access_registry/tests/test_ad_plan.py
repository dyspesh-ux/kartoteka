"""AD change plan (stage 1): what goes into the plan, the guards, approval by ИБ, the script."""

import frappe
from frappe.utils import add_days, today

from access_registry.active_directory import plan as adplan
from access_registry.active_directory.script import ps, render
from access_registry.registry import api
from access_registry.tests.test_it_assets import SnipeFixture
from access_registry.tests.test_permissions import make_user

DOMAIN = "TSTAD"


class TestAdPlan(SnipeFixture):
	def setUp(self):
		super().setUp()
		frappe.db.delete("AD Change Plan")
		frappe.db.delete("AD Change Plan Item")
		self.domain = frappe.get_doc("AD Domain", DOMAIN)
		self.domain.update(
			{
				"plan_disabled_ou": "OU=Disabled,DC=corp,DC=example,DC=local",
				"plan_max_disable_share": 100,
				"plan_protected_groups": "Domain Admins",
				"plan_grace_days": 1,
			}
		)
		self.domain.save()
		self.linked = frappe.get_all(
			"AD Account",
			filters={"domain": DOMAIN, "enabled": 1, "missing_in_source": 0, "person": ["is", "set"]},
			fields=["name", "person", "sam_account_name", "title"],
			order_by="sam_account_name",
		)
		self.assertGreaterEqual(len(self.linked), 3)

	def dismiss(self, person, days_ago=5, missing=0):
		for e in frappe.get_all("Employment", filters={"person": person}, pluck="name"):
			frappe.db.set_value(
				"Employment",
				e,
				{"status": "Уволен", "termination_date": add_days(today(), -days_ago), "missing": missing},
			)

	def items(self, plan, action=None):
		return [i for i in plan["items"] if action is None or i["action"] == action]

	def test_disable_only_when_every_job_is_over(self):
		a, b, c = self.linked[:3]
		self.dismiss(a.person)
		self.dismiss(b.person, days_ago=0)  # dismissed today: grace day not over
		self.dismiss(c.person, missing=1)  # gone from the ZUP export: check by hand
		plan = adplan.build(DOMAIN)
		disabled = {i["account"] for i in self.items(plan, adplan.DISABLE)}
		self.assertIn(a.name, disabled)
		self.assertNotIn(b.name, disabled)
		self.assertNotIn(c.name, disabled)
		self.assertTrue(any("пропало из выгрузки" in s for s in plan["skipped"]))
		item = next(i for i in plan["items"] if i["account"] == a.name)
		self.assertIn("других мест работы нет", item["reason"])

		# dismissed from a part-time job only: another job remains — no disabling
		jobs = frappe.get_all("Employment", filters={"person": a.person}, pluck="name")
		frappe.db.set_value("Employment", jobs[0], {"status": "Работает", "termination_date": None})
		self.assertNotIn(a.name, {i["account"] for i in self.items(adplan.build(DOMAIN), adplan.DISABLE)})

	def test_updates_from_hr(self):
		from access_registry.access_catalog.access_report import main_places

		places = main_places([x.person for x in self.linked])
		a = next(
			x
			for x in self.linked
			if (places.get(x.person) or {}).get("position_title")
			and (places.get(x.person) or {}).get("status") in adplan.WORKING
		)
		frappe.db.set_value("AD Account", a.name, "title", "Старая должность")
		plan = adplan.build(DOMAIN)
		item = next(
			i for i in self.items(plan, adplan.UPDATE) if i["account"] == a.name and i["attribute"] == "title"
		)
		self.assertEqual(item["before"], "Старая должность")
		self.assertTrue(item["after"])
		self.domain.reload()
		self.domain.plan_attr_title = 0
		self.domain.save()
		self.assertFalse(
			[i for i in self.items(adplan.build(DOMAIN), adplan.UPDATE) if i["attribute"] == "title"]
		)

	def test_guards(self):
		a, b = self.linked[:2]
		self.dismiss(a.person)
		self.dismiss(b.person)
		self.domain.reload()
		self.domain.plan_exclude = f"{a.sam_account_name}\nOU=Nowhere,DC=corp,DC=example,DC=local"
		self.domain.save()
		plan = adplan.build(DOMAIN)
		self.assertNotIn(a.name, {i["account"] for i in plan["items"]})
		self.assertTrue(any("Не трогать" in s for s in plan["skipped"]))
		# members of protected groups
		row = frappe.get_doc("AD Account", b.name)
		row.append("groups", {"group_name": "Domain Admins"})
		row.save(ignore_permissions=True)
		self.assertNotIn(b.name, {i["account"] for i in adplan.build(DOMAIN)["items"]})
		# too many dismissals at once look like a broken export
		self.domain.reload()
		self.domain.update({"plan_exclude": "", "plan_protected_groups": "", "plan_max_disable_share": 1})
		self.domain.save()
		self.assertRaises(adplan.PlanStopped, adplan.build, DOMAIN)

	def test_four_eyes_script_and_states(self):
		a = self.linked[0]
		self.dismiss(a.person)
		it = make_user("ad-plan-it@registry.test")
		ib = make_user("ad-plan-ib@registry.test")
		api.save_profile({"profile_name": "ИТ: AD", "sections": {"control": 2}, "members": [it]})
		api.save_profile(
			{"profile_name": "ИБ: AD", "sections": {"control": 1}, "ad_approve": 1, "members": [ib]}
		)

		frappe.set_user(it)
		self.assertTrue(api.bootstrap()["can"]["ad_plans"])
		name = api.create_ad_plan(DOMAIN)
		self.assertRaises(frappe.PermissionError, api.decide_ad_plan, name, "Одобрен")
		self.assertRaises(frappe.ValidationError, api.download_ad_script, name)  # not approved yet

		frappe.set_user(ib)
		plan = api.ad_plan(name)
		self.assertTrue(plan["can"]["decide"])
		keep = next(i for i in plan["items"] if i["account"] == a.name)
		drop = [i["name"] for i in plan["items"] if i["name"] != keep["name"]]
		api.set_ad_plan_items(name, drop)
		self.assertRaises(frappe.ValidationError, api.decide_ad_plan, name, "Отклонён")  # needs a reason
		self.assertEqual(api.decide_ad_plan(name, "Одобрен", "ок"), "Одобрен")
		self.assertRaises(frappe.ValidationError, api.decide_ad_plan, name, "Отклонён", "поздно")

		doc = frappe.get_doc("AD Change Plan", name)
		script = render(doc, frappe.get_doc("AD Domain", DOMAIN))
		self.assertIn("[switch]$Apply", script)
		self.assertIn(frappe.db.get_value("AD Account", a.name, "object_guid"), script)
		self.assertIn("OU=Disabled,DC=corp,DC=example,DC=local", script)
		self.assertEqual(script.count("Action = '"), 1)  # unchecked rows are not in the script
		api.download_ad_script(name)
		self.assertTrue(frappe.response["filecontent"].startswith("\ufeff"))
		self.assertTrue(
			frappe.get_all(
				"Comment", filters={"reference_name": name, "content": ["like", "%Скрипт скачал%"]}
			)
		)

		frappe.set_user("Administrator")
		frappe.db.set_value("AD Account", a.name, "enabled", 0)  # the next AD load after the script
		frappe.set_user(ib)
		state = next(i["state"] for i in api.ad_plan(name)["items"] if i["account"] == a.name)
		self.assertEqual(state, "выполнено")

	def test_no_rights(self):
		user = make_user("ad-plan-none@registry.test")
		api.save_profile({"profile_name": "Только люди", "sections": {"people": 1}, "members": [user]})
		frappe.set_user(user)
		self.assertFalse(api.bootstrap()["can"]["ad_plans"])
		self.assertRaises(frappe.PermissionError, api.ad_plans)
		self.assertRaises(frappe.PermissionError, api.create_ad_plan, DOMAIN)

	def test_ps_literal(self):
		self.assertEqual(ps("O'Brien"), "'O''Brien'")
		self.assertEqual(ps("$(Remove-Item C:\\)"), "'$(Remove-Item C:\\)'")  # single quotes: nothing expands
		self.assertEqual(ps("a’b"), "'a’’b'")
