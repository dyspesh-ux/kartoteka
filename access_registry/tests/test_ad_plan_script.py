"""The generated PowerShell script against a fake ActiveDirectory module: dry run, apply, rollback.

Runs where PowerShell 7 (pwsh) is installed; elsewhere it is skipped.
"""

import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from access_registry.active_directory.script import render

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "ad_plan")
PWSH = shutil.which("pwsh") or (os.path.exists("/tmp/pwsh/pwsh") and "/tmp/pwsh/pwsh")


def item(**kw):
	return frappe._dict({"include": 1, "reason": "x", "attribute": "", "before": "", "after": "", **kw})


@unittest.skipUnless(PWSH, "PowerShell (pwsh) is not installed")
class TestAdPlanScript(FrappeTestCase):
	def setUp(self):
		self.dir = tempfile.mkdtemp()
		self.state = os.path.join(self.dir, "state.json")
		self.log = os.path.join(self.dir, "calls.log")
		shutil.copy(os.path.join(FIXTURES, "state.json"), self.state)
		items = [
			item(action="Отключить", object_guid="g-1", sam_account_name="ivanov", reason="уволен"),
			item(
				action="Изменить",
				object_guid="g-2",
				sam_account_name="petrova",
				attribute="title",
				before="Бухгалтер",
				after="Главный бухгалтер O'Neil $(whoami)",
			),
			item(
				action="Изменить",
				object_guid="g-3",
				sam_account_name="sidorov",
				attribute="department",
				before="Склад",
				after="Логистика",
			),
			item(action="Отключить", object_guid="g-4", sam_account_name="admin1"),
			item(action="Отключить", object_guid="g-5", sam_account_name="ghost"),
			item(include=0, action="Отключить", object_guid="g-6", sam_account_name="excluded"),
			item(
				action="Изменить",
				object_guid="g-7",
				sam_account_name="empty",
				attribute="company",
				before="",
				after="ООО «Бета»",
			),
		]
		plan = SimpleNamespace(
			name="ADP-TEST",
			items=items,
			creation=now_datetime(),
			owner="it@x",
			data_as_of=None,
			approved_by="ib@x",
			decided_on=now_datetime(),
		)
		domain = frappe._dict(
			name="SK", dns_name="dc1", plan_disabled_ou="OU=Disabled,DC=sk,DC=ru", plan_remove_groups=1
		)
		self.script = os.path.join(self.dir, "ADP-TEST.ps1")
		with open(self.script, "w", encoding="utf-8-sig") as fh:
			fh.write(render(plan, domain))

	def tearDown(self):
		shutil.rmtree(self.dir, ignore_errors=True)

	def pwsh(self, path, *args):
		env = {**os.environ, "PSModulePath": FIXTURES, "FAKE_AD_STATE": self.state, "FAKE_AD_LOG": self.log}
		out = subprocess.run(
			[PWSH, "-NoProfile", "-NonInteractive", "-File", path, *args],
			capture_output=True,
			text=True,
			env=env,
			timeout=120,
		)
		self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
		return out.stdout + out.stderr

	def calls(self):
		if not os.path.exists(self.log):
			return []
		with open(self.log, encoding="utf-8") as fh:
			lines = fh.read().splitlines()
		os.remove(self.log)
		return lines

	def users(self):
		with open(self.state, encoding="utf-8") as fh:
			return json.load(fh)["users"]

	def test_dry_run_apply_rollback(self):
		before = self.users()
		out = self.pwsh(self.script)
		self.assertIn("ПРОВЕРКА", out)
		self.assertEqual(self.calls(), [])  # the dry run changes nothing

		out = self.pwsh(self.script, "-Apply")
		calls = self.calls()
		self.assertIn("Disable g-1", calls)
		self.assertIn("Move g-1 -> OU=Disabled,DC=sk,DC=ru", calls)
		self.assertEqual(sum(c.startswith("RemoveGroup g-1") for c in calls), 2)
		self.assertIn("Replace g-2 title = Главный бухгалтер O'Neil $(whoami)", calls)  # not executed
		self.assertFalse([c for c in calls if " g-3 " in c + " "])  # changed by someone since the plan
		self.assertFalse([c for c in calls if "g-4" in c])  # adminCount=1
		self.assertFalse([c for c in calls if "g-6" in c])  # unchecked in the plan
		self.assertIn("выполнено 3, пропущено 3, ошибок 0", out)
		self.assertFalse(self.users()["g-1"]["Enabled"])

		rollback = glob.glob(os.path.join(self.dir, "ad-plan-ADP-TEST-*-rollback.ps1"))
		self.assertEqual(len(rollback), 1)
		self.pwsh(rollback[0])
		after = self.users()
		for guid, user in before.items():
			for key, value in user.items():
				got = after[guid].get(key)
				if isinstance(value, list):
					value, got = sorted(value), sorted(got or [])
				self.assertEqual(value or None, got or None, f"{guid}.{key}")
