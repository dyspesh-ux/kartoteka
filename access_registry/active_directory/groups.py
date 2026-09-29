"""Effective AD group membership: direct groups of an account plus all groups they are nested in."""

from collections import defaultdict

import frappe


def parent_map() -> dict:
	"""{group: {groups it is a direct member of}}."""
	parents = defaultdict(set)
	for row in frappe.get_all(
		"AD Account Group",
		filters={"parenttype": "AD Group", "parentfield": "parent_groups"},
		fields=["parent", "group"],
		limit_page_length=0,
	):
		parents[row.parent].add(row.group)
	return parents


def closure(groups, parents=None) -> set:
	"""The groups and every group they are nested in (cycles are safe)."""
	parents = parent_map() if parents is None else parents
	result, stack = set(), list(groups)
	while stack:
		group = stack.pop()
		if group in result:
			continue
		result.add(group)
		stack.extend(parents.get(group, ()))
	return result


def effective_account_groups(only_enabled: bool = True) -> dict:
	"""{AD Account: {effective groups}} for accounts present in AD (enabled only by default)."""
	parents = parent_map()
	conditions = "a.missing_in_source = 0" + (" and a.enabled = 1" if only_enabled else "")
	direct = defaultdict(set)
	for account, group in frappe.db.sql(
		f"""select g.parent, g.`group` from `tabAD Account Group` g
		join `tabAD Account` a on a.name = g.parent
		where g.parenttype = 'AD Account' and {conditions}"""
	):
		direct[account].add(group)
	return {account: closure(groups, parents) for account, groups in direct.items()}
