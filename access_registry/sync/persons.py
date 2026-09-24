"""Person statuses, merge candidates and manual merging of people."""

import frappe
from frappe import _
from frappe.model.rename_doc import rename_doc

ACTIVE_EMPLOYMENT_STATUSES = ("Работает", "Увольняется")
MAIN_EMPLOYMENT_KIND = "ОсновноеМестоРаботы"
PARENTAL_LEAVE_CATEGORY = "ОтпускПоУходу"


def compute_person_state(employments: list) -> dict:
	"""Person status, presence and the external-part-time flag from all employments (all sources)."""
	statuses = [e.status for e in employments]
	active = [e for e in employments if e.status in ACTIVE_EMPLOYMENT_STATUSES]
	if active:
		status = "Работает"
	elif "Не принят" in statuses:
		status = "Не принят"
	elif statuses and all(s == "Нет в выгрузке" for s in statuses):
		status = "Нет в выгрузке"
	elif statuses:
		status = "Уволен"
	else:
		status = ""

	long_absence = bool(active) and all(
		e.category == PARENTAL_LEAVE_CATEGORY and not e.actually_working for e in active
	)
	return {
		"status": status,
		"presence": "Длительное отсутствие" if long_absence else "На месте",
		"external_part_time_only": int(
			bool(active) and not any(e.employment_kind_code == MAIN_EMPLOYMENT_KIND for e in active)
		),
	}


def pair_key(a: str, b: str) -> str:
	return "|".join(sorted([a, b]))


def create_merge_candidate(person_a: str, person_b: str, reason: str) -> bool:
	"""Suggests merging ``person_b`` (new) into ``person_a`` (existing).

	A pair that already exists in any status — including «Разные люди» — is not suggested again.
	"""
	if person_a == person_b:
		return False
	key = pair_key(person_a, person_b)
	if frappe.db.exists("Person Merge Candidate", {"pair_key": key}):
		return False
	frappe.get_doc(
		{
			"doctype": "Person Merge Candidate",
			"person_a": person_a,
			"person_b": person_b,
			"reason": reason,
			"status": "Открыт",
		}
	).insert(ignore_permissions=True)
	return True


def merge_persons(keep: str, drop: str, candidate: str | None = None) -> str:
	"""Merges Person ``drop`` into ``keep``. The UUID (name) of ``keep`` never changes.

	Source identifiers of ``drop`` move to ``keep``; every Link to ``drop`` in any DocType
	(employments, events, absences, department heads, legal entity directors, candidates,
	and whatever later stages add) is repointed by ``rename_doc(merge=True)``,
	then ``drop`` is deleted.
	"""
	if keep == drop:
		frappe.throw(_("Нельзя склеить человека с самим собой"))
	a = frappe.get_doc("Person", keep)
	b = frappe.get_doc("Person", drop)

	existing = {(row.source, row.person_guid) for row in a.source_ids}
	moved = []
	for row in b.source_ids:
		if (row.source, row.person_guid) in existing:
			continue
		a.append(
			"source_ids",
			{
				"source": row.source,
				"person_guid": row.person_guid,
				"last_name": row.last_name,
				"first_name": row.first_name,
				"middle_name": row.middle_name,
				"birth_date": row.birth_date,
			},
		)
		moved.append(f"{row.source}:{row.person_guid}")
	a.save(ignore_permissions=True, ignore_version=False)

	# Detach rows from B first: rename_doc(merge) deletes B together with its children.
	frappe.db.delete("Person Source ID", {"parent": drop, "parenttype": "Person"})

	rename_doc(
		"Person",
		drop,
		keep,
		merge=True,
		force=True,
		ignore_permissions=True,
		show_alert=False,
		rebuild_search=False,
	)

	_refresh_candidates(keep, drop, candidate)
	_refresh_after_merge(keep)

	a = frappe.get_doc("Person", keep)
	a.add_comment(
		"Comment",
		_("Склеен с человеком {0} ({1}). Перенесены идентификаторы источников: {2}").format(
			drop, b.full_name or "—", ", ".join(moved) or "—"
		),
	)
	return keep


def _refresh_candidates(keep: str, drop: str, candidate: str | None):
	rows = frappe.get_all("Person Merge Candidate", filters={"person_a": keep}, pluck="name")
	rows += frappe.get_all("Person Merge Candidate", filters={"person_b": keep}, pluck="name")
	for name in sorted(set(rows)):
		doc = frappe.get_doc("Person Merge Candidate", name)
		if doc.person_a == doc.person_b:
			# The pair collapsed into A–A: it is resolved by this merge.
			doc.status = "Склеено"
			doc.merged_uuid = drop
			doc.pair_key = f"{keep}|{drop}|{doc.name}"
			doc.save(ignore_permissions=True, ignore_version=False)
			continue
		new_key = pair_key(doc.person_a, doc.person_b)
		if new_key == doc.pair_key:
			continue
		duplicate = frappe.db.get_value(
			"Person Merge Candidate", {"pair_key": new_key, "name": ["!=", doc.name]}, ["name", "status"], as_dict=True
		)
		if duplicate:
			# The same pair already exists; keep «Разные люди» if either said so.
			if doc.status == "Разные люди" and duplicate.status == "Открыт":
				frappe.db.set_value("Person Merge Candidate", duplicate.name, "status", "Разные люди")
			frappe.delete_doc("Person Merge Candidate", doc.name, ignore_permissions=True, force=True)
			continue
		doc.pair_key = new_key
		doc.save(ignore_permissions=True, ignore_version=False)

	if candidate and frappe.db.exists("Person Merge Candidate", candidate):
		doc = frappe.get_doc("Person Merge Candidate", candidate)
		if doc.status != "Склеено" or doc.merged_uuid != drop:
			doc.status = "Склеено"
			doc.merged_uuid = drop
			doc.save(ignore_permissions=True, ignore_version=False)


def _refresh_after_merge(keep: str):
	person = frappe.get_doc("Person", keep)
	for idx, row in enumerate(person.source_ids, start=1):
		row.idx = idx
	employments = frappe.get_all(
		"Employment",
		filters={"person": keep},
		fields=["name", "status", "employment_kind_code", "category", "actually_working"],
		limit_page_length=0,
	)
	if employments:
		person.update(compute_person_state(employments))
	person.save(ignore_permissions=True, ignore_version=False)

	# Department head fields were repointed by raw SQL: recompute head / conflict.
	departments = set()
	for fieldname in ("zup_head", "manual_head", "head_candidate", "head"):
		departments.update(frappe.get_all("HR Department", filters={fieldname: keep}, pluck="name"))
	for name in sorted(departments):
		frappe.get_doc("HR Department", name).save(ignore_permissions=True, ignore_version=False)
