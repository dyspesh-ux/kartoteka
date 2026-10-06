"""Every scheduled job of the app gets its own Scheduled Job Type."""

from collections import Counter

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry import hooks


class TestScheduler(FrappeTestCase):
	def methods(self):
		for value in hooks.scheduler_events.values():
			for methods in value.values() if isinstance(value, dict) else [value]:
				yield from methods

	def test_scheduler_names(self):
		# Frappe names a Scheduled Job Type by the last two parts of the method: equal names mean
		# that one job silently replaces another (AD, Bitrix24 and Snipe-IT once all were
		# «sync.scheduled_sync» and only one of them ran)
		names = Counter(".".join(m.split(".")[-2:]) for m in self.methods())
		self.assertEqual([n for n, count in names.items() if count > 1], [])
		for method in self.methods():
			self.assertTrue(callable(frappe.get_attr(method)), method)

	def test_jobs_exist_after_migrate(self):
		jobs = set(
			frappe.get_all(
				"Scheduled Job Type", filters={"method": ["like", "access_registry.%"]}, pluck="method"
			)
		)
		self.assertEqual(set(self.methods()) - jobs, set())
