"""Who actually has access to a shared folder.

Effective access of an AD account to a folder is the smaller of two levels:
- the privilege on the shared folder in DSM (read/write, from the account or any of its groups);
- the Windows ACL of the folder (allow entries of the account, its groups — nested too — or «Все»).
A deny entry for the account or any of its groups closes access. Folders that are not recorded
have the rights of the nearest recorded folder above them (the collector records only changes).
"""

from collections import defaultdict

import frappe

from access_registry.active_directory.groups import effective_account_groups
from access_registry.file_shares.synology import LEVEL_RANK

ALL = "Все"


class ShareAccess:
	def __init__(self):
		self.accounts = {
			a.name: a
			for a in frappe.get_all(
				"AD Account",
				filters={"enabled": 1, "missing_in_source": 0},
				fields=["name", "display_name", "sam_account_name", "domain", "person"],
				limit_page_length=0,
			)
		}
		self.netbios = {
			d.name: d.netbios_name or d.name
			for d in frappe.get_all("AD Domain", fields=["name", "netbios_name"])
		}
		self.groups_of = {a: g for a, g in effective_account_groups().items() if a in self.accounts}
		self.members = defaultdict(set)
		for account, groups in self.groups_of.items():
			for group in groups:
				self.members[group].add(account)
		self.privileges = defaultdict(list)
		for row in frappe.get_all(
			"File Share Privilege",
			filters={"parenttype": "File Share"},
			fields=["parent", "principal", "principal_type", "ad_account", "ad_group", "level"],
			limit_page_length=0,
		):
			self.privileges[row.parent].append(row)

	def accounts_of(self, row) -> set:
		if row.ad_account:
			return {row.ad_account} if row.ad_account in self.accounts else set()
		if row.ad_group:
			return set(self.members.get(row.ad_group, ()))
		if row.principal_type == ALL:
			return set(self.accounts)
		return set()

	def share_levels(self, share) -> dict | None:
		"""{account: level} granted on the shared folder; None when the share has no parsed privileges."""
		rows = self.privileges.get(share)
		if not rows:
			return None
		levels, denied = {}, set()
		for row in rows:
			for account in self.accounts_of(row):
				if row.level == "Запрет":
					denied.add(account)
				elif LEVEL_RANK[row.level] > LEVEL_RANK.get(levels.get(account), -1):
					levels[account] = row.level
		for account in denied:
			levels.pop(account, None)
		return levels

	def folder_access(self, folder, entries, share_levels) -> dict:
		"""{account: (level, via)} for one folder."""
		granted, denied = {}, set()
		for e in entries:
			accounts = self.accounts_of(e)
			if e.allow == "Запретить":
				denied |= accounts
				continue
			for account in accounts:
				current = granted.get(account)
				if not current or LEVEL_RANK[e.level] > LEVEL_RANK[current[0]]:
					granted[account] = (e.level, e.principal)
		result = {}
		for account, (level, via) in granted.items():
			if account in denied:
				continue
			if share_levels is not None:
				share_level = share_levels.get(account)
				if not share_level:
					continue  # no privilege on the shared folder: DSM does not let in
				if LEVEL_RANK[share_level] < LEVEL_RANK[level]:
					level = share_level
			result[account] = (level, via)
		return result

	def rows(self, filters=None) -> list[dict]:
		filters = frappe._dict(filters or {})
		conditions = {"missing_in_source": 0}
		for key in ("server", "share"):
			if filters.get(key):
				conditions[key] = filters[key]
		if filters.get("path"):
			conditions["path"] = ["like", f"%{filters.path}%"]
		folders = frappe.get_all(
			"Folder ACL",
			filters=conditions,
			fields=["name", "server", "share", "path", "depth"],
			order_by="share, path",
			limit_page_length=0,
		)
		entries = defaultdict(list)
		for row in frappe.get_all(
			"Folder ACL Entry",
			filters={"parenttype": "Folder ACL", "parent": ["in", [f.name for f in folders] or [""]]},
			fields=["parent", "principal", "principal_type", "ad_account", "ad_group", "allow", "level"],
			limit_page_length=0,
		):
			entries[row.parent].append(row)
		share_names = dict(
			frappe.get_all("File Share", fields=["name", "share_name"], as_list=True, limit_page_length=0)
		)
		share_cache = {}
		result = []
		for folder in folders:
			if folder.share not in share_cache:
				share_cache[folder.share] = self.share_levels(folder.share)
			access = self.folder_access(folder, entries[folder.name], share_cache[folder.share])
			for account, (level, via) in access.items():
				a = self.accounts[account]
				if filters.get("person") and a.person != filters.person:
					continue
				result.append(
					{
						"folder": folder.name,
						"server": folder.server,
						"share": folder.share,
						"share_name": share_names.get(folder.share),
						"path": folder.path,
						"account": account,
						"account_name": a.display_name,
						"login": f"{self.netbios.get(a.domain, a.domain)}\\{a.sam_account_name}",
						"person": a.person,
						"level": level,
						"via": via,
					}
				)
		return result
