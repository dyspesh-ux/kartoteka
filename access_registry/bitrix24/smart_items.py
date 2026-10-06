"""Items of Bitrix24 smart processes, for example helpdesk requests: their state only.

What is read: stage, category, source, who asked, who is responsible, dates and deadlines. The
description of the problem, files, comments and the timeline are not read. Open items are loaded
all, closed ones (success or failure stages) for ``history_days``. The mirror is rebuilt on every
load: items deleted in Bitrix24 or closed long ago disappear from it.

Which item fields mean «who asked», «category», «deadline» is set on «B24 Smart Process»; empty
fields are guessed from the type and the title of the fields (``detect_fields``) and saved there.
"""

import json
import re
import traceback
from datetime import timedelta

import frappe
from frappe.utils import cint, now_datetime

from access_registry.bitrix24.client import B24Error
from access_registry.bitrix24.sync import b24_date, b24_datetime, make_client, src_hash
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user

LOG_KIND = "Смарт-процессы Битрикс24"
SAVEPOINT = "b24_smart_sync"
OPEN, DONE, CANCELLED = "Открыта", "Завершена", "Отменена"
STATE_OF_SEMANTICS = {"S": DONE, "F": CANCELLED}
BASE_SELECT = [
	"id",
	"title",
	"stageId",
	"categoryId",
	"createdTime",
	"updatedTime",
	"movedTime",
	"assignedById",
]
# item field → (types, title pattern, required title match)
ROLES = {
	"requester_field": (("employee", "user"), r"обрат|заявител|инициатор|автор|кто", False),
	"source_field": (("enumeration",), r"источник", True),
	"category_field": (("enumeration",), r"каталог|категор|услуг|тип|раздел", False),
	"deadline_field": (("date", "datetime"), r"срок|дедлайн|deadline|план", True),
	"done_field": (("date", "datetime"), r"выполн|заверш|закры|решен", True),
}


class SmartGuardTripped(frappe.ValidationError):
	pass


# --------------------------------------------------------------------------- jobs


def scheduled_smart_sync():
	for name in frappe.get_all("B24 Smart Process", filters={"enabled": 1}, pluck="name"):
		enqueue(name)


def enqueue(process: str):
	return frappe.enqueue(
		"access_registry.bitrix24.smart_items.run_process_sync",
		queue="long",
		timeout=get_settings().job_timeout,
		job_id=f"b24_smart_sync::{process}",
		deduplicate=True,
		process=process,
	)


def run_process_sync(process: str, commit: bool = True, fetch=None, client=None):
	"""Reads the items of one smart process and rebuilds their mirror. Returns the Sync Log.

	``fetch(process_doc) -> dict`` (the shape of ``fetch_process``) replaces the network in tests.
	"""
	settings = get_settings()
	log = frappe.get_doc(
		{"doctype": "Sync Log", "kind": LOG_KIND, "status": "В процессе", "started": now_datetime()}
	).insert(ignore_permissions=True)
	if commit:
		frappe.db.commit()
	stats, messages = {}, []
	previous_user = frappe.session.user
	_switch_user(settings.sync_user, messages)
	frappe.db.savepoint(SAVEPOINT)
	doc = frappe.get_doc("B24 Smart Process", process)
	status = "Ошибка"
	try:
		if fetch:
			data = fetch(doc)
		else:
			client = client or make_client(frappe.get_doc("B24 Portal", doc.portal))
			data = fetch_process(doc, client)
		importer = ProcessImport(doc)
		stats = importer.run(data)
		messages += importer.warnings
		status = "Успех"
	except SmartGuardTripped as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except B24Error as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		messages.append(f"Битрикс24: {e}" + ("\nВебхуку нужен доступ crm." if e.is_access else ""))
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		messages.append(traceback.format_exc())
	_switch_user(previous_user, None)
	log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps({"process": process, **stats}, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	log.save(ignore_permissions=True)
	frappe.db.set_value(
		"B24 Smart Process",
		process,
		"last_status",
		f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})",
		update_modified=False,
	)
	if commit:
		frappe.db.commit()
	return log


# --------------------------------------------------------------------------- reading


def detect_fields(fields: dict, current: dict | None = None) -> dict:
	"""Guesses the item fields of each role from their type and title; set ones are kept.

	``fields`` — the answer of crm.item.fields: {code: {type, title, isMultiple, ...}}."""
	chosen = {k: v for k, v in (current or {}).items() if v}
	used = set(chosen.values())
	for role, (types, pattern, must_match) in ROLES.items():
		if chosen.get(role):
			continue
		candidates = [
			code
			for code, f in fields.items()
			if code.lower().startswith("uf") and f.get("type") in types and code not in used
		]
		if role == "requester_field":
			candidates = [c for c in candidates if not fields[c].get("isMultiple")]
		titled = [c for c in candidates if re.search(pattern, _title(fields[c]), re.IGNORECASE)]
		if role == "category_field":
			titled = [c for c in titled if not re.search(ROLES["source_field"][1], _title(fields[c]), re.I)]
		pick = (titled or ([] if must_match else candidates))[:1]
		if pick:
			chosen[role] = pick[0]
			used.add(pick[0])
	return chosen


def _title(f: dict) -> str:
	return str(f.get("title") or f.get("formLabel") or f.get("listLabel") or "")


def fetch_process(doc, client) -> dict:
	"""Everything the mirror needs as plain dicts (the shape of the tests' fixture)."""
	entity_type_id = cint(doc.entity_type_id)
	smart_type = next((t for t in client.crm_types() if cint(t.get("entityTypeId")) == entity_type_id), None)
	if smart_type is None:
		raise B24Error(
			"NOT_FOUND", f"смарт-процесса с entityTypeId {entity_type_id} на портале нет (или нет прав)"
		)
	fields = (client.result("crm.item.fields", {"entityTypeId": entity_type_id}) or {}).get("fields") or {}
	categories = client.result("crm.category.list", {"entityTypeId": entity_type_id}) or {}
	categories = categories.get("categories", categories) if isinstance(categories, dict) else categories
	stage_lists = client.call_many(
		"crm.status.list",
		[
			{
				"filter": {"ENTITY_ID": f"DYNAMIC_{entity_type_id}_STAGE_{c.get('id')}"},
				"order": {"SORT": "ASC"},
			}
			for c in categories or []
		],
	)
	funnels = []
	for c, stages in zip(categories or [], stage_lists, strict=False):
		if isinstance(stages, B24Error):
			raise stages
		funnels.append({"id": c.get("id"), "name": c.get("name"), "stages": list(stages or [])})

	codes = detect_fields(fields, {role: (doc.get(role) or "").strip() for role in ROLES})
	select = BASE_SELECT + [c for c in codes.values() if c]
	stages = [s for f in funnels for s in f["stages"]]
	open_ids = [s.get("STATUS_ID") for s in stages if (s.get("SEMANTICS") or "") not in STATE_OF_SEMANTICS]
	closed_ids = [s.get("STATUS_ID") for s in stages if (s.get("SEMANTICS") or "") in STATE_OF_SEMANTICS]
	base = {"entityTypeId": entity_type_id, "select": select, "order": {"id": "ASC"}}
	if stages:
		since = now_datetime() - timedelta(days=cint(doc.history_days) or 365)
		items = []
		if open_ids:
			items += client.list_all("crm.item.list", {**base, "filter": {"@stageId": open_ids}}, key="items")
		if closed_ids:
			items += client.list_all(
				"crm.item.list",
				{
					**base,
					"filter": {"@stageId": closed_ids, ">=movedTime": since.strftime("%Y-%m-%dT%H:%M:%S")},
				},
				key="items",
			)
	else:  # stages are switched off for the process: everything
		items = client.list_all("crm.item.list", base, key="items")
	# an item closed between the two requests comes in both: the later answer wins
	items = list({cint(i.get("id")): i for i in items}.values())
	return {"type": smart_type, "fields": fields, "funnels": funnels, "codes": codes, "items": items}


# --------------------------------------------------------------------------- mirror


class ProcessImport:
	def __init__(self, doc):
		self.doc = doc
		self.portal = frappe.get_doc("B24 Portal", doc.portal)
		self.warnings = []

	def run(self, data: dict) -> dict:
		doc = self.doc
		fields = data.get("fields") or {}
		codes = detect_fields(fields, {role: (doc.get(role) or "").strip() for role in ROLES})
		codes.update({k: v for k, v in (data.get("codes") or {}).items() if v and not codes.get(k)})
		for role in ROLES:
			code = codes.get(role)
			if code and code not in fields and fields:
				self.warnings.append(f"Поля {code} ({role}) у смарт-процесса нет — проверьте настройку")
			if not code:
				self.warnings.append(
					f"Не найдено поле для «{doc.meta.get_label(role)}» — укажите его вручную"
				)
		options = {
			code: {
				str(i.get("ID")): i.get("VALUE")
				for i in (fields.get(code) or {}).get("items") or []
				if isinstance(i, dict)
			}
			for code in (codes.get("category_field"), codes.get("source_field"))
			if code
		}
		stages, stage_rows = {}, []
		for funnel in data.get("funnels") or []:
			for sort, s in enumerate(funnel.get("stages") or []):
				state = STATE_OF_SEMANTICS.get(s.get("SEMANTICS") or "", OPEN)
				stages[s.get("STATUS_ID")] = (s.get("NAME") or s.get("STATUS_ID"), state)
				stage_rows.append(
					{
						"stage_id": s.get("STATUS_ID"),
						"stage_name": s.get("NAME"),
						"state": state,
						"funnel": funnel.get("name"),
						"sort": sort + 1,
					}
				)
		items = data.get("items") or []
		existing = dict(
			frappe.get_all(
				"B24 Smart Item",
				filters={"smart_process": doc.name},
				fields=["uid", "src_hash"],
				as_list=True,
			)
		)
		if not items and len(existing) > 10:
			raise SmartGuardTripped(
				f"Битрикс24 не вернул ни одной заявки, а в реестре их {len(existing)}: загрузка остановлена, "
				"данные не изменены. Проверьте права вебхука и настройку смарт-процесса."
			)
		users = self._users(items, codes)
		stats = {"items": len(items), "created": 0, "updated": 0, "deleted": 0}
		seen = set()
		for raw in items:
			row = self._row(raw, codes, options, stages, users)
			if row["uid"] in seen:  # the same item twice in one answer
				continue
			seen.add(row["uid"])
			row["src_hash"] = src_hash(row)
			if row["uid"] not in existing:
				frappe.get_doc({"doctype": "B24 Smart Item", **row}).insert(ignore_permissions=True)
				stats["created"] += 1
			elif existing[row["uid"]] != row["src_hash"]:
				item = frappe.get_doc("B24 Smart Item", row["uid"])
				item.update(row)
				item.save(ignore_permissions=True)
				stats["updated"] += 1
		for uid in set(existing) - seen:
			frappe.delete_doc("B24 Smart Item", uid, ignore_permissions=True, force=True)
			stats["deleted"] += 1

		doc.reload()
		for role in ROLES:
			if codes.get(role) and not doc.get(role):
				doc.set(role, codes[role])
		if not doc.title:
			doc.title = (data.get("type") or {}).get("title")
		doc.set("stages", stage_rows)
		doc.last_sync = now_datetime()
		doc.items_count = len(seen)
		doc.open_count = frappe.db.count("B24 Smart Item", {"smart_process": doc.name, "state": OPEN})
		doc.flags.ignore_version = True
		doc.save(ignore_permissions=True)
		stats["open"] = doc.open_count
		return stats

	def _users(self, items, codes) -> dict:
		ids = set()
		for raw in items:
			for key in ("assignedById", codes.get("requester_field")):
				if key and _user_id(raw.get(key)):
					ids.add(_user_id(raw.get(key)))
		if not ids:
			return {}
		rows = frappe.get_all(
			"B24 User",
			filters={"portal": self.portal.name, "b24_id": ["in", sorted(ids)]},
			fields=["name", "b24_id", "full_name", "person"],
		)
		return {str(r.b24_id): r for r in rows}

	def _row(self, raw, codes, options, stages, users) -> dict:
		doc = self.doc
		item_id = cint(raw.get("id"))
		stage_id = raw.get("stageId") or ""
		stage_name, state = stages.get(stage_id, (stage_id, OPEN))
		moved = b24_datetime(raw.get("movedTime"))

		def value(role):
			code = codes.get(role)
			return raw.get(code) if code else None

		def option(role):
			v = value(role)
			if isinstance(v, list):
				v = v[0] if v else None
			return options.get(codes.get(role), {}).get(str(v), v) if v not in (None, "") else None

		requester = users.get(_user_id(value("requester_field")) or "")
		assigned = users.get(_user_id(raw.get("assignedById")) or "")
		portal_url = (self.portal.portal_url or "").rstrip("/")
		return {
			"uid": f"{doc.name}:{item_id}",
			"smart_process": doc.name,
			"item_id": item_id,
			"title": (raw.get("title") or f"#{item_id}")[:140],
			"stage_id": stage_id,
			"stage_name": stage_name,
			"state": state,
			"category": option("category_field"),
			"source": option("source_field"),
			"created_at": b24_datetime(raw.get("createdTime")),
			"updated_at": b24_datetime(raw.get("updatedTime")),
			"moved_at": moved,
			"closed_at": moved if state != OPEN else None,
			"deadline": b24_date(value("deadline_field")),
			"done_date": b24_date(value("done_field")),
			"requester_b24": requester.name if requester else None,
			"requester": requester.person if requester else None,
			"requester_name": requester.full_name if requester else _unknown(value("requester_field")),
			"assigned_b24": assigned.name if assigned else None,
			"assignee": assigned.person if assigned else None,
			"assigned_name": assigned.full_name if assigned else _unknown(raw.get("assignedById")),
			"url": f"{portal_url}/crm/type/{doc.entity_type_id}/details/{item_id}/" if portal_url else None,
		}


def _user_id(value) -> str | None:
	"""Bitrix24 user in an item: 15, "15" or "user_15"."""
	if isinstance(value, list):
		value = value[0] if value else None
	if value in (None, "", 0, "0"):
		return None
	digits = re.sub(r"\D", "", str(value))
	return digits or None


def _unknown(value) -> str | None:
	user_id = _user_id(value)
	return f"пользователь {user_id}" if user_id else None
