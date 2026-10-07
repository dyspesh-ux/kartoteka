"""Names behind the references in smart process items: a position from a catalog smart process, a
department, a company, an element of a list (office address).

crm.item.list gives ids only. Fields of type «crm» name the smart process they point to in their
settings (DYNAMIC_1086: Y); the value is the bare id or «T43e_15» (the type in hex) when several
types are allowed. Companies (mycompanyId, crm_company) are read with crm.company.list, list
elements (iblock_element) with lists.element.get. A reference that cannot be read stays «№15»: the
registry says what it could not resolve instead of failing the load.
"""

import re

from access_registry.bitrix24.client import B24Error

LIST_TYPES = ("lists", "lists_socnet", "bitrix_processes")


def ref_id(value):
	"""15, "15", "T43e_15", "DYNAMIC_1086_15" → "15"; lists take the first value."""
	if isinstance(value, list):
		value = value[0] if value else None
	if value in (None, "", 0, "0", False):
		return None
	m = re.search(r"(\d+)$", str(value))
	return m.group(1) if m else None


def _entity_type(field: dict, value) -> int | None:
	m = re.match(r"T([0-9a-f]+)_\d+$", str(value or ""), re.I)
	if m:
		return int(m.group(1), 16)
	settings = field.get("settings") or {}
	types = [
		int(k.split("_")[1]) for k, v in settings.items() if k.startswith("DYNAMIC_") and v in ("Y", True)
	]
	return types[0] if len(types) == 1 else None


def resolve(client, fields: dict, codes: dict, items: list, warnings: list) -> dict:
	"""{code: {id: title}} for the reference fields among ``codes``; departments also give
	``__org__``: {department id: company title} (the organization of a department item)."""
	refs = {}
	wanted_items, wanted_companies, wanted_elements = {}, set(), {}
	for code in {c for c in codes.values() if c}:
		field = fields.get(code) or {}
		kind = field.get("type")
		for item in items:
			value = item.get(code)
			rid = ref_id(value)
			if not rid:
				continue
			if kind == "crm":
				entity = _entity_type(field, value)
				if entity:
					wanted_items.setdefault(entity, set()).add(rid)
			elif kind == "crm_company":
				wanted_companies.add(rid)
			elif kind == "iblock_element":
				iblock = (field.get("settings") or {}).get("IBLOCK_ID")
				if iblock:
					wanted_elements.setdefault(str(iblock), set()).add(rid)

	titles_by_type, company_of_item = {}, {}
	for entity, ids in wanted_items.items():
		try:
			rows = client.list_all(
				"crm.item.list",
				{
					"entityTypeId": entity,
					"filter": {"@id": sorted(ids, key=int)},
					"select": ["id", "title", "mycompanyId", "companyId"],
				},
				key="items",
			)
		except B24Error as e:
			warnings.append(f"Справочник (смарт-процесс {entity}) не прочитан: {e}")
			continue
		titles_by_type[entity] = {str(r.get("id")): r.get("title") for r in rows}
		for r in rows:
			company = ref_id(r.get("mycompanyId")) or ref_id(r.get("companyId"))
			if company:
				company_of_item[(entity, str(r.get("id")))] = company
				wanted_companies.add(company)

	companies = {}
	if wanted_companies:
		try:
			rows = client.list_all(
				"crm.company.list",
				{"filter": {"@ID": sorted(wanted_companies, key=int)}, "select": ["ID", "TITLE"]},
			)
			companies = {str(r.get("ID")): r.get("TITLE") for r in rows}
		except B24Error as e:
			warnings.append(f"Компании не прочитаны: {e}")

	elements = {}
	for iblock, ids in wanted_elements.items():
		for list_type in LIST_TYPES:
			try:
				rows = client.list_all(
					"lists.element.get",
					{
						"IBLOCK_TYPE_ID": list_type,
						"IBLOCK_ID": iblock,
						"FILTER": {"ID": sorted(ids, key=int)},
					},
				)
			except B24Error:
				continue
			elements[iblock] = {str(r.get("ID")): r.get("NAME") for r in rows}
			break
		else:
			warnings.append(
				f"Список {iblock} не прочитан (нужен доступ вебхука lists): показаны номера элементов"
			)

	for code in {c for c in codes.values() if c}:
		field = fields.get(code) or {}
		kind = field.get("type")
		if kind == "crm":
			mapping, orgs = {}, {}
			for item in items:
				value = item.get(code)
				rid, entity = ref_id(value), _entity_type(field, value)
				if rid and entity:
					mapping[rid] = titles_by_type.get(entity, {}).get(rid)
					company = company_of_item.get((entity, rid))
					if company and companies.get(company):
						orgs[rid] = companies[company]
			refs[code] = mapping
			refs.setdefault("__org__", {}).update({f"{code}:{k}": v for k, v in orgs.items()})
		elif kind == "crm_company":
			refs[code] = companies
		elif kind == "iblock_element":
			refs[code] = elements.get(str((field.get("settings") or {}).get("IBLOCK_ID")), {})
	return refs
