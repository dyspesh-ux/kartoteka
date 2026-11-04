"""Suggests merge candidates for Persons created before their data aligned (e.g. birth date added later)."""

import frappe

from access_registry.sync.persons import rescan_merge_candidates


def execute():
	created = rescan_merge_candidates()
	# Counters only: no names or other personal data in the log.
	frappe.logger("access_registry").info(
		"rescan_merge_candidates: full={full} partial={partial}".format(**created)
	)
