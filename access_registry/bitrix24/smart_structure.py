"""Structure of the smart processes of a Bitrix24 portal, for deciding what the registry should show.

Only the description: types, funnels, stages, fields (code, type, title, list options) and the number
of items. No items, no values of the records. The result is a JSON file the administrator gets
in the attachments of the portal card and sends to the developers.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from access_registry.bitrix24.client import B24Error

# fields every item has: their description is the same for every smart process
SYSTEM_FIELDS = {
	"id",
	"xmlId",
	"title",
	"createdBy",
	"updatedBy",
	"movedBy",
	"createdTime",
	"updatedTime",
	"movedTime",
	"categoryId",
	"opened",
	"stageId",
	"previousStageId",
	"begindate",
	"closedate",
	"companyId",
	"contactId",
	"contactIds",
	"opportunity",
	"isManualOpportunity",
	"taxValue",
	"currencyId",
	"mycompanyId",
	"sourceId",
	"sourceDescription",
	"webformId",
	"assignedById",
	"observers",
	"lastActivityBy",
	"lastActivityTime",
	"utmSource",
	"utmMedium",
	"utmCampaign",
	"utmContent",
	"utmTerm",
	"entityTypeId",
}


def _field(code: str, f: dict) -> dict:
	result = {
		"code": code,
		"title": f.get("title") or f.get("formLabel") or f.get("listLabel") or code,
		"type": f.get("type"),
		"multiple": bool(f.get("isMultiple")),
		"required": bool(f.get("isRequired")),
		"system": code in SYSTEM_FIELDS,
	}
	if f.get("items"):  # list options: labels only
		result["options"] = [i.get("VALUE") for i in f["items"] if isinstance(i, dict)]
	settings = f.get("settings") or {}
	if f.get("type") in ("crm_entity", "crm") and settings:
		result["links_to"] = [k for k, v in settings.items() if v == "Y"]
	return result


def _value(answer, key):
	value = answer.get(key)
	if isinstance(value, B24Error):
		raise value
	return value


def describe(client) -> dict:
	"""Two batches for the whole portal instead of a call per funnel: on a portal with dozens of
	smart processes the sequential calls took minutes."""
	types = client.crm_types()
	result = {"generated": str(now_datetime()), "smart_processes": []}
	commands = {}
	for t in types:
		entity_type_id = t.get("entityTypeId")
		commands[f"cat{entity_type_id}"] = ("crm.category.list", {"entityTypeId": entity_type_id})
		commands[f"fld{entity_type_id}"] = ("crm.item.fields", {"entityTypeId": entity_type_id})
		commands[f"cnt{entity_type_id}"] = (
			"crm.item.list",
			{"entityTypeId": entity_type_id, "select": ["id"]},
		)
	totals = {}
	answers = client.batch(commands, totals=totals) if commands else {}

	items, stage_commands = [], {}
	for t in types:
		entity_type_id = t.get("entityTypeId")
		item = {
			"entityTypeId": entity_type_id,
			"title": t.get("title"),
			"code": t.get("code"),
			"funnels_enabled": t.get("isCategoriesEnabled") in (True, "Y"),
			"stages_enabled": t.get("isStagesEnabled") in (True, "Y"),
			"linked": {
				k: t.get(k)
				for k in ("isClientEnabled", "isMycompanyEnabled", "isDocumentsEnabled", "isObserversEnabled")
				if k in t
			},
			"funnels": [],
			"fields": [],
		}
		try:
			categories = _value(answers, f"cat{entity_type_id}") or {}
			categories = (
				categories.get("categories", categories) if isinstance(categories, dict) else categories
			)
			for c in categories or []:
				key = f"st{entity_type_id}_{c.get('id')}"
				stage_commands[key] = (
					"crm.status.list",
					{
						"filter": {"ENTITY_ID": f"DYNAMIC_{entity_type_id}_STAGE_{c.get('id')}"},
						"order": {"SORT": "ASC"},
					},
				)
				item["funnels"].append(
					{
						"id": c.get("id"),
						"name": c.get("name"),
						"default": c.get("isDefault") in (True, "Y"),
						"stages": key,  # filled from the second batch
					}
				)
			fields = (_value(answers, f"fld{entity_type_id}") or {}).get("fields") or {}
			item["fields"] = [_field(code, f) for code, f in fields.items()]
			_value(answers, f"cnt{entity_type_id}")
			item["items"] = int(totals.get(f"cnt{entity_type_id}") or 0)
		except B24Error as e:
			item["error"] = str(e)
		items.append(item)

	stages = client.batch(stage_commands) if stage_commands else {}
	for item in items:
		for funnel in item["funnels"]:
			value = stages.get(funnel["stages"])
			if isinstance(value, B24Error):
				item["error"] = str(value)
				value = None
			funnel["stages"] = [
				{"id": s.get("STATUS_ID"), "name": s.get("NAME"), "semantics": s.get("SEMANTICS") or ""}
				for s in value or []
			]
		result["smart_processes"].append(item)
	return result


@frappe.whitelist()
def start(portal: str):
	"""Portal card → «Структура смарт-процессов»: collected in the background (a web request would
	hit the proxy timeout, HTTP 504), the file is attached to the portal card."""
	frappe.only_for(("System Manager", "Registry Admin"))
	frappe.get_doc("B24 Portal", portal).check_permission("read")
	frappe.enqueue(
		"access_registry.bitrix24.smart_structure.build",
		queue="long",
		timeout=1800,
		job_id=f"b24_smart_structure::{portal}",
		deduplicate=True,
		portal=portal,
		user=frappe.session.user,
	)
	return _("Собираю структуру смарт-процессов. Файл появится во вложениях карточки портала.")


def build(portal: str, user: str | None = None):
	from access_registry.bitrix24.sync import make_client

	try:
		data = describe(make_client(frappe.get_doc("B24 Portal", portal)))
	except Exception as e:
		frappe.log_error(title=f"Bitrix24: структура смарт-процессов {portal}")
		if user:
			frappe.publish_realtime(
				"b24_smart_structure", {"portal": portal, "error": str(e)}, user=user, after_commit=True
			)
		return None
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"smart-processes-{frappe.scrub(portal)}-{now_datetime():%Y%m%d-%H%M}.json",
			"attached_to_doctype": "B24 Portal",
			"attached_to_name": portal,
			"is_private": 1,
			"content": json.dumps(data, ensure_ascii=False, indent=1),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	if user:
		frappe.publish_realtime(
			"b24_smart_structure",
			{"portal": portal, "file_url": file.file_url, "count": len(data["smart_processes"])},
			user=user,
		)
	return file.name
