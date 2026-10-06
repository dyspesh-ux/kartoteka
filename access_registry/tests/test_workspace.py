"""Every DocType of the app can be reached from the menu (the workspaces of the desk)."""

import glob
import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

APP = os.path.dirname(os.path.dirname(__file__))


class TestWorkspaces(FrappeTestCase):
	def test_every_doctype_has_a_menu_link(self):
		links = set()
		for path in glob.glob(os.path.join(APP, "access_registry", "workspace", "*", "*.json")):
			with open(path, encoding="utf-8") as fh:
				ws = json.load(fh)
			links |= {x.get("link_to") for x in ws["links"]} | {x.get("link_to") for x in ws["shortcuts"]}
		doctypes = set()
		for path in glob.glob(os.path.join(APP, "**", "doctype", "*", "*.json"), recursive=True):
			with open(path, encoding="utf-8") as fh:
				d = json.load(fh)
			if d.get("doctype") == "DocType" and not d.get("istable"):
				doctypes.add(d["name"])
		self.assertEqual(sorted(doctypes - links), [], "add them to a workspace (menu)")

	def test_workspace_targets_exist(self):
		for path in glob.glob(os.path.join(APP, "access_registry", "workspace", "*", "*.json")):
			with open(path, encoding="utf-8") as fh:
				ws = json.load(fh)
			for x in ws["links"] + ws["shortcuts"]:
				target, kind = x.get("link_to"), x.get("link_type") or x.get("type")
				if target and kind in ("DocType", "Report"):
					self.assertTrue(frappe.db.exists(kind, target), f"{ws['name']}: {kind} {target}")
