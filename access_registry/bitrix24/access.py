"""Who actually has access to a Bitrix24 section: access codes expanded to users.

Codes: U<id> — user, D<id> — department, DR<id> — department with subdepartments,
G<id> — user group, SG<id>_A/_E/_K — workgroup owner/moderators/all members,
AU/UA/IU — all employees.
"""

from collections import defaultdict

import frappe

from access_registry.bitrix24.sync import parse_access_code

ROLE_RANK = {"Владелец": "A", "Модератор": "E", "Участник": "K"}


class AccessExpander:
	def __init__(self, portal: str):
		self.portal = portal
		self.users = {
			u.b24_id: u
			for u in frappe.get_all(
				"B24 User",
				filters={"portal": portal, "missing_in_source": 0},
				fields=["name", "b24_id", "full_name", "person", "active", "user_type", "is_admin"],
				limit_page_length=0,
			)
		}
		self.children = defaultdict(list)
		self.dept_members = defaultdict(set)
		for d in frappe.get_all(
			"B24 Department",
			filters={"portal": portal},
			fields=["b24_id", "parent_department"],
			limit_page_length=0,
		):
			if d.parent_department:
				self.children[d.parent_department.split(":", 1)[1]].append(d.b24_id)
		for row in frappe.db.sql(
			"""select u.b24_id, d.department from `tabB24 User Department` d
			join `tabB24 User` u on u.name = d.parent where u.portal = %s""",
			portal,
		):
			self.dept_members[row[1].split(":", 1)[1]].add(row[0])
		self.group_members = defaultdict(list)
		for row in frappe.db.sql(
			"""select g.b24_id, u.b24_id, m.role from `tabB24 Workgroup Member` m
			join `tabB24 Workgroup` g on g.name = m.parent
			join `tabB24 User` u on u.name = m.user
			where g.portal = %s and g.missing_in_source = 0""",
			portal,
		):
			self.group_members[row[0]].append((row[1], ROLE_RANK.get(row[2], "K")))
		self.user_groups = defaultdict(set)
		for via, code in frappe.get_all(
			"B24 Access Grant",
			filters={"portal": portal, "resource_type": "Группа пользователей", "missing_in_source": 0},
			fields=["via", "access_code"],
			as_list=True,
			limit_page_length=0,
		):
			gid = (via or "").rsplit(" ", 1)[-1]
			self.user_groups[gid].add(code[1:])

	def subtree(self, dept: str, seen=None) -> set:
		seen = seen if seen is not None else set()
		if dept in seen:
			return seen
		seen.add(dept)
		for child in self.children.get(dept, []):
			self.subtree(child, seen)
		return seen

	def expand(self, code: str) -> list[str]:
		"""User IDs (b24_id) behind an access code."""
		kind, number, suffix = parse_access_code(code)
		if kind == "U":
			result = {number}
		elif kind == "D":
			result = set(self.dept_members.get(number, set()))
		elif kind == "DR":
			result = set()
			for dept in self.subtree(number):
				result |= self.dept_members.get(dept, set())
		elif kind == "G":
			if number == "2":
				result = set(self.users)
			else:
				result = set(self.user_groups.get(number, set()))
		elif kind == "SG":
			allowed = {"A": {"A"}, "E": {"A", "E"}}.get(suffix, {"A", "E", "K"})
			result = {u for u, role in self.group_members.get(number, []) if role in allowed}
		elif kind in ("AU", "UA", "IU"):
			result = {u for u, row in self.users.items() if (row.user_type or "employee") == "employee"}
		else:
			result = set()
		return sorted((u for u in result if u in self.users), key=lambda x: int(x) if x.isdigit() else 0)


def effective_grants(portal: str, filters: dict | None = None) -> list[dict]:
	"""Grants of the portal expanded to users: one row per (grant, user)."""
	filters = dict(filters or {})
	grant_filters = {"portal": portal, "missing_in_source": 0}
	for key in ("resource_type", "resource"):
		if filters.get(key):
			grant_filters[key] = ["like", f"%{filters[key]}%"] if key == "resource" else filters[key]
	expander = AccessExpander(portal)
	rows = []
	for grant in frappe.get_all(
		"B24 Access Grant",
		filters=grant_filters,
		fields=[
			"name",
			"resource_type",
			"resource",
			"permission",
			"via",
			"access_code",
			"principal",
			"negative",
		],
		order_by="resource_type, resource, via",
		limit_page_length=0,
	):
		for b24_id in expander.expand(grant.access_code):
			user = expander.users[b24_id]
			rows.append(
				{
					**grant,
					"grant": grant.name,
					"user": user.name,
					"user_name": user.full_name,
					"person": user.person,
					"active": user.active,
				}
			)
	return rows
