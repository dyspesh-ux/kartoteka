"""Structure of the smart processes of a Bitrix24 portal, for deciding what the registry should show.

Only the description: types, funnels, stages, fields (code, type, title, list options) and the number
of items. No items, no values of the records. The result is a JSON file the administrator downloads
from the portal card and sends to the developers.
"""

import json

import frappe
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


def describe(client) -> dict:
	types = client.crm_types()
	result = {"generated": str(now_datetime()), "smart_processes": []}
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
			categories = client.result("crm.category.list", {"entityTypeId": entity_type_id}) or {}
			categories = (
				categories.get("categories", categories) if isinstance(categories, dict) else categories
			)
			for c in categories or []:
				stages = client.result(
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
						"stages": [
							{
								"id": s.get("STATUS_ID"),
								"name": s.get("NAME"),
								"semantics": s.get("SEMANTICS") or "",
							}
							for s in stages or []
						],
					}
				)
			fields = (client.result("crm.item.fields", {"entityTypeId": entity_type_id}) or {}).get(
				"fields"
			) or {}
			item["fields"] = [_field(code, f) for code, f in fields.items()]
			counted = client.call("crm.item.list", {"entityTypeId": entity_type_id, "select": ["id"]})
			item["items"] = int(counted.get("total") or 0)
		except B24Error as e:
			item["error"] = str(e)
		result["smart_processes"].append(item)
	return result


@frappe.whitelist()
def download(portal: str):
	"""The structure as a JSON file (portal card → «Структура смарт-процессов»)."""
	frappe.only_for(("System Manager", "Registry Admin"))
	from access_registry.bitrix24.sync import make_client

	data = describe(make_client(frappe.get_doc("B24 Portal", portal)))
	frappe.response["filename"] = f"smart-processes-{portal}.json"
	frappe.response["filecontent"] = json.dumps(data, ensure_ascii=False, indent=1)
	frappe.response["type"] = "download"
