"""Reports on shared folders."""

import frappe
from frappe import _
from frappe.utils import cint

from access_registry.file_shares.access import ShareAccess
from access_registry.file_shares.synology import LEVEL_RANK


def col(fieldname, label, fieldtype="Data", width=160, options=None):
	c = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "width": width}
	if options:
		c["options"] = options
	return c


def _statuses(rows) -> dict:
	persons = [r["person"] for r in rows if r.get("person")]
	return dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", persons or [""]]},
			fields=["name", "status"],
			as_list=True,
			limit_page_length=0,
		)
	)


def share_access(filters=None):
	"""Доступ к общим папкам: кто и с какими правами (с учётом прав шары, ACL и вложенных групп)."""
	filters = frappe._dict(filters or {})
	rows = ShareAccess().rows(filters)
	statuses = _statuses(rows)
	names = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", [r["person"] for r in rows if r["person"]] or [""]]},
			fields=["name", "full_name"],
			as_list=True,
			limit_page_length=0,
		)
	)
	min_rank = LEVEL_RANK.get(filters.get("min_level") or "", 0)
	data = []
	for r in rows:
		r["person_status"] = statuses.get(r["person"]) or (_("не привязан") if not r["person"] else "")
		r["full_name"] = names.get(r["person"]) or r["account_name"]
		if LEVEL_RANK[r["level"]] < min_rank:
			continue
		if cint(filters.get("only_not_working")) and (not r["person"] or r["person_status"] == "Работает"):
			continue
		data.append(r)
	data.sort(key=lambda r: (r["share_name"] or "", r["path"] or "", r["full_name"] or ""))
	columns = [
		col("share_name", _("Общая папка"), width=150),
		col("path", _("Папка"), width=260),
		col("full_name", _("Сотрудник"), width=220),
		col("person_status", _("Статус"), width=100),
		col("level", _("Доступ"), width=120),
		col("via", _("Через"), width=200),
		col("login", _("Учётка"), width=160),
		col("person", _("Карточка"), "Link", 140, "Person"),
		col("folder", _("Права папки"), "Link", 140, "Folder ACL"),
	]
	return columns, data


def share_issues(filters=None):
	"""Нарушения в правах на общие папки: прямые права, доступ для всех, запреты, неизвестные учётки,
	локальные учётки NAS, отключённое наследование, доступ у неработающих."""
	filters = frappe._dict(filters or {})
	conditions = {"missing_in_source": 0, "issues": ["is", "set"]}
	if filters.get("server"):
		conditions["server"] = filters.server
	data = []
	share_names = dict(
		frappe.get_all("File Share", fields=["name", "share_name"], as_list=True, limit_page_length=0)
	)
	for f in frappe.get_all(
		"Folder ACL", filters=conditions, fields=["name", "share", "path", "issues"], limit_page_length=0
	):
		for line in (f.issues or "").splitlines():
			kind, _sep, detail = line.partition(": ")
			data.append(
				{
					"share_name": share_names.get(f.share),
					"path": f.path,
					"issue": kind,
					"detail": detail,
					"folder": f.name,
				}
			)
	rows = ShareAccess().rows({"server": filters.get("server")})
	statuses = _statuses(rows)
	names = dict(
		frappe.get_all(
			"Person",
			filters={"name": ["in", list(statuses) or [""]]},
			fields=["name", "full_name"],
			as_list=True,
			limit_page_length=0,
		)
	)
	for r in rows:
		status = statuses.get(r["person"])
		if r["person"] and status and status != "Работает":
			data.append(
				{
					"share_name": r["share_name"],
					"path": r["path"],
					"issue": _("доступ у неработающего"),
					"detail": f"{names.get(r['person'])} ({status}): {r['level']} через {r['via']}",
					"folder": r["folder"],
					"person": r["person"],
				}
			)
	if filters.get("issue"):
		data = [d for d in data if filters.issue.lower() in d["issue"].lower()]
	data.sort(key=lambda d: (d["share_name"] or "", d["path"] or "", d["issue"]))
	columns = [
		col("share_name", _("Общая папка"), width=150),
		col("path", _("Папка"), width=260),
		col("issue", _("Замечание"), width=260),
		col("detail", _("Подробно"), width=360),
		col("folder", _("Права папки"), "Link", 140, "Folder ACL"),
		col("person", _("Сотрудник"), "Link", 140, "Person"),
	]
	return columns, data
