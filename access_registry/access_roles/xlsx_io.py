"""The role model to and from Excel: entitlements and access roles, one workbook, two sheets.

«Права» — Название*, Система*, Риск, Привилегированное, Владелец, Что даёт, AD-группа, Профиль 1С, Группа Б24.
«Роли» — Роль*, Вид, Описание, Статус, Права, Обязательные. Loading is all-or-nothing:
if any row has an error, nothing is saved and every error is listed with its sheet and row.
"""

import io
from collections import defaultdict

import frappe
from frappe import _

from access_registry.access_catalog.linking import NameMatcher
from access_registry.sync.normalize import normalize_name

SYSTEMS = ("1С", "Active Directory", "Битрикс24", "Другое")
RISKS = ("Низкий", "Средний", "Высокий", "Критичный")
ROLE_KINDS = ("Должностная", "Базовая", "Дополнительная")
ROLE_STATUSES = ("Действует", "Черновик", "Архив")
REQUIREMENTS = ("Обязательно", "По необходимости")
YES = {"да", "yes", "1", "true", "истина", "+", "y", "д"}
REQUIRED_MARK = "*"

ENTITLEMENT_COLUMNS = [
	("title", "Название*"),
	("system", "Система*"),
	("risk", "Риск"),
	("privileged", "Привилегированное"),
	("owner", "Владелец"),
	("description", "Что даёт"),
	("ad_group", "AD-группа"),
	("ib_profile", "Профиль 1С"),
	("b24_workgroup", "Группа Б24"),
]
ROLE_COLUMNS = [
	("role", "Роль*"),
	("kind", "Вид"),
	("description", "Описание"),
	("status", "Статус"),
	("entitlements", "Права"),
	("mandatory", "Обязательные"),
]
SHEETS = {"Права": ENTITLEMENT_COLUMNS, "Роли": ROLE_COLUMNS}


def _label_key(label) -> str:
	return normalize_name(str(label or "").replace(REQUIRED_MARK, "").split("(")[0])


def _clean(value):
	if isinstance(value, str):
		value = value.strip()
		return value or None
	return value


def read_workbook(content: bytes) -> dict:
	"""{sheet: [(row number, {key: value})]} by header labels (the order of columns does not matter)."""
	from openpyxl import load_workbook

	wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	result = {}
	for title, columns in SHEETS.items():
		if title not in wb.sheetnames:
			result[title] = []
			continue
		ws = wb[title]
		rows = ws.iter_rows(values_only=True)
		header = next(rows, None) or ()
		by_label = {_label_key(label): key for key, label in columns}
		index = {
			i: by_label.get(_label_key(h)) for i, h in enumerate(header) if h and by_label.get(_label_key(h))
		}
		sheet_rows = []
		for number, values in enumerate(rows, start=2):
			row = {key: _clean(values[i]) for i, key in index.items() if i < len(values)}
			if any(v not in (None, "") for v in row.values()):
				sheet_rows.append((number, row))
		result[title] = sheet_rows
	return result


def template_workbook() -> bytes:
	"""The workbook of the current role model: headers plus the catalogued entitlements and roles."""
	from openpyxl import Workbook

	wb = Workbook()
	ws = wb.active
	ws.title = "Права"
	ws.append([label for _key, label in ENTITLEMENT_COLUMNS])
	ad_groups = dict(frappe.get_all("AD Group", fields=["name", "group_name"], as_list=True))
	ib_profiles = dict(frappe.get_all("IB Access Profile", fields=["name", "profile_name"], as_list=True))
	workgroups = dict(frappe.get_all("B24 Workgroup", fields=["name", "group_name"], as_list=True))
	persons = dict(frappe.get_all("Person", fields=["name", "full_name"], as_list=True))
	for e in frappe.get_all(
		"Entitlement",
		fields=[
			"title",
			"system",
			"risk",
			"privileged",
			"owner_person",
			"description",
			"ad_group",
			"ib_profile",
			"b24_workgroup",
		],
		order_by="system, title",
		limit_page_length=0,
	):
		ws.append(
			[
				e.title,
				e.system,
				e.risk,
				"да" if e.privileged else None,
				persons.get(e.owner_person),
				e.description,
				ad_groups.get(e.ad_group),
				ib_profiles.get(e.ib_profile),
				workgroups.get(e.b24_workgroup),
			]
		)
	ws = wb.create_sheet("Роли")
	ws.append([label for _key, label in ROLE_COLUMNS])
	titles = dict(frappe.get_all("Entitlement", fields=["name", "title"], as_list=True))
	for r in frappe.get_all(
		"Access Role",
		fields=["name", "role_name", "kind", "description", "status"],
		order_by="role_name",
		limit_page_length=0,
	):
		rows = frappe.get_all(
			"Access Role Entitlement",
			filters={"parent": r.name, "parenttype": "Access Role"},
			fields=["entitlement", "requirement"],
			limit_page_length=0,
		)
		mandatory = [titles[row.entitlement] for row in rows if row.requirement == "Обязательно"]
		optional = [titles[row.entitlement] for row in rows if row.requirement != "Обязательно"]
		ws.append(
			[
				r.role_name,
				r.kind,
				r.description,
				r.status,
				"; ".join(optional + mandatory),
				"; ".join(mandatory),
			]
		)
	stream = io.BytesIO()
	wb.save(stream)
	return stream.getvalue()


class RoleModelImport:
	"""Validates the workbook against the registry and applies it. Errors → nothing is saved."""

	def __init__(self, content: bytes):
		self.sheets = read_workbook(content)
		self.errors: list[str] = []
		self.counters = defaultdict(int)
		self.names = NameMatcher()
		# (normalized title, system) → entitlement name: the catalog as known before + created by this file
		self.entitlements = {}
		for e in frappe.get_all("Entitlement", fields=["name", "title", "system"], limit_page_length=0):
			self.entitlements[(normalize_name(e.title), e.system)] = e.name
		self.roles = set(frappe.get_all("Access Role", pluck="name"))
		self.binding = {
			"ad_group": self._index("AD Group", "group_name"),
			"ib_profile": self._index("IB Access Profile", "profile_name"),
			"b24_workgroup": self._index("B24 Workgroup", "group_name"),
		}

	def _index(self, doctype: str, title_field: str) -> dict:
		"""normalized title → record name; ambiguous titles map to None (an error on use)."""
		result = {}
		for name, title in frappe.get_all(doctype, fields=["name", title_field], as_list=True):
			key = normalize_name(title)
			result[key] = None if key in result else name
		return result

	def error(self, sheet, row, text):
		self.errors.append(_("Лист «{0}», строка {1}: {2}").format(sheet, row, text))

	def owner(self, value, sheet, row):
		if not value:
			return None
		person, note = self.names.match(str(value).strip())
		if not person:
			self.error(sheet, row, _("владелец: {0}").format(note))
		return person

	def link(self, doctype_field, value, sheet, row, what):
		if not value:
			return None
		found = self.binding[doctype_field].get(normalize_name(str(value).strip()))
		if not found:
			self.error(sheet, row, _("{0} «{1}» не найдено в зеркале").format(what, value))
		return found

	def run(self, apply: bool) -> dict:
		"""Checks everything; when ``apply`` and there are no errors, saves. Returns a summary."""
		savepoint = "role_model_import"
		frappe.db.savepoint(savepoint)
		try:
			self.import_entitlements()
			self.import_roles()
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			raise
		if self.errors or not apply:
			frappe.db.rollback(save_point=savepoint)
		return {
			"errors": self.errors,
			"counters": dict(self.counters),
			"applied": bool(apply and not self.errors),
		}

	# ------------------------------------------------------------ entitlements

	def import_entitlements(self):
		sheet = "Права"
		seen = set()
		for number, row in self.sheets.get(sheet) or []:
			title = row.get("title")
			system = row.get("system")
			if not title:
				self.error(sheet, number, _("не указано название права"))
				continue
			if not system:
				self.error(sheet, number, _("не указана система"))
				continue
			if system not in SYSTEMS:
				self.error(
					sheet, number, _("система «{0}» — нужно одно из: {1}").format(system, ", ".join(SYSTEMS))
				)
				continue
			key = (normalize_name(title), system)
			if key in seen:
				self.error(sheet, number, _("дубль права «{0}» ({1}) в файле").format(title, system))
				continue
			seen.add(key)
			if row.get("risk") and row["risk"] not in RISKS:
				self.error(
					sheet, number, _("риск «{0}» — нужно одно из: {1}").format(row["risk"], ", ".join(RISKS))
				)
				continue
			owner = self.owner(row.get("owner"), sheet, number)
			links = {
				"ad_group": self.link("ad_group", row.get("ad_group"), sheet, number, "AD-группа"),
				"ib_profile": self.link("ib_profile", row.get("ib_profile"), sheet, number, "профиль 1С"),
				"b24_workgroup": self.link(
					"b24_workgroup", row.get("b24_workgroup"), sheet, number, "группа Битрикс24"
				),
			}
			existing = self.entitlements.get(key)
			if existing:
				# only the cells present in the file replace the stored values
				updates = {k: v for k, v in links.items() if row.get(k) not in (None, "")}
				if row.get("risk"):
					updates["risk"] = row["risk"]
				if row.get("privileged") not in (None, ""):
					updates["privileged"] = 1 if str(row["privileged"]).lower() in YES else 0
				if row.get("description"):
					updates["description"] = row["description"]
				if owner:
					updates["owner_person"] = owner
				doc = frappe.get_doc("Entitlement", existing)
				doc.update(updates)
				doc.save(ignore_permissions=True)
				self.counters["прав обновлено"] += 1
			else:
				doc = frappe.get_doc(
					{
						"doctype": "Entitlement",
						"title": title,
						"system": system,
						"risk": row.get("risk") or "Средний",
						"privileged": 1 if str(row.get("privileged") or "").lower() in YES else 0,
						"owner_person": owner,
						"description": row.get("description"),
						**links,
					}
				).insert(ignore_permissions=True)
				self.entitlements[key] = doc.name
				self.counters["прав создано"] += 1

	# ------------------------------------------------------------ roles

	def import_roles(self):
		sheet = "Роли"
		for number, row in self.sheets.get(sheet) or []:
			name = row.get("role")
			if not name:
				self.error(sheet, number, _("не указано название роли"))
				continue
			kind = row.get("kind") or "Должностная"
			if kind not in ROLE_KINDS:
				self.error(
					sheet, number, _("вид «{0}» — нужно одно из: {1}").format(kind, ", ".join(ROLE_KINDS))
				)
				continue
			status = row.get("status") or "Черновик"
			if status not in ROLE_STATUSES:
				self.error(
					sheet,
					number,
					_("статус «{0}» — нужно одно из: {1}").format(status, ", ".join(ROLE_STATUSES)),
				)
				continue
			given = self.titles(row.get("entitlements"), sheet, number)
			mandatory = set(self.titles(row.get("mandatory"), sheet, number))
			unknown = mandatory - set(given)
			if unknown:
				self.error(
					sheet,
					number,
					_("обязательные права не входят в список прав роли: {0}").format(
						"; ".join(sorted(unknown))
					),
				)
				continue
			rows = [
				{"entitlement": e, "requirement": "Обязательно" if e in mandatory else "По необходимости"}
				for e in given
			]
			if name in self.roles:
				doc = frappe.get_doc("Access Role", name)
				doc.kind = kind
				doc.status = status
				if row.get("description"):
					doc.description = row["description"]
				doc.set("entitlements", rows)
				doc.save(ignore_permissions=True)
				self.counters["ролей обновлено"] += 1
			else:
				frappe.get_doc(
					{
						"doctype": "Access Role",
						"role_name": name,
						"kind": kind,
						"status": status,
						"description": row.get("description"),
						"entitlements": rows,
					}
				).insert(ignore_permissions=True)
				self.roles.add(name)
				self.counters["ролей создано"] += 1

	def titles(self, value, sheet, row) -> list[str]:
		"""Semicolon-separated entitlement titles → entitlement names (via this file + the catalog)."""
		if not value:
			return []
		result = []
		for title in str(value).split(";"):
			title = title.strip()
			if not title:
				continue
			matches = [
				name for (norm, _s), name in self.entitlements.items() if norm == normalize_name(title)
			]
			if len(matches) > 1:
				self.error(
					sheet,
					row,
					_("название права «{0}» встречается в каталоге несколько раз — уточните").format(title),
				)
				continue
			if not matches:
				self.error(
					sheet,
					row,
					_("право «{0}» нет в файле и в каталоге — добавьте его на лист «Права»").format(title),
				)
				continue
			result.append(matches[0])
		return result
