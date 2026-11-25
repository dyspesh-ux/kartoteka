"""Tests of the ZKBio (СКУД) module: scaffold, client config, persons/events sync, reports."""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestZKBioScaffold(FrappeTestCase):
	def test_module_scaffold(self):
		# Task 1 scope: the server DocType. Person/Event and the workspace join in Tasks 2–3.
		self.assertTrue(frappe.db.exists("DocType", "ZKBio Server"))


class TestPassNos(FrappeTestCase):
	def test_pass_nos_parsing(self):
		from access_registry.zkbio.sync import pass_nos

		server = frappe._dict(pass_event_nos=None)
		self.assertEqual(pass_nos(server), {0})
		server.pass_event_nos = " 0, 202 ,208 "
		self.assertEqual(pass_nos(server), {0, 202, 208})
		server.pass_event_nos = "0,abc, 14x"
		self.assertEqual(pass_nos(server), {0})  # «abc»/«14x» проигнорированы

	def test_missing_driver_message(self):
		import sys
		from unittest import mock

		from access_registry.zkbio import pg_client

		with mock.patch.dict(sys.modules, {"psycopg2": None}):
			with self.assertRaises(frappe.ValidationError) as ctx:
				pg_client._psycopg2()
		self.assertIn("psycopg2-binary", str(ctx.exception))
