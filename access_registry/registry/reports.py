"""Reports of the registry inside the app /registry: the same Script Reports as in the desk.

The catalog gives Russian titles, groups and explanations; filters are read from the report's own JS
file (frappe.query_reports[...].filters), so the app and the desk never disagree about them. The report
runs through Frappe's generate_report_result; who may run it is decided by app_access (section
«Отчёты» and the optional list of reports of an access profile), not by the desk roles of the report.
"""

import json
import os
import re

import frappe
from frappe import _
from frappe.utils import cint

# group → [(report, title, explanation, needs personal data)]
CATALOG = [
	(
		"Доступы в 1С",
		[
			(
				"IB Access Report",
				"Полный отчёт по доступам 1С",
				"Кто в какой базе работает: профили, организации, роли, вход",
				False,
			),
			("IB Profile Users", "Пользователи профиля", "У кого есть выбранный профиль доступа 1С", False),
			(
				"IB Rights Changes",
				"Кто менял права",
				"Журнал изменений пользователей и профилей 1С за период",
				False,
			),
			(
				"IB All Organizations Access",
				"Доступ ко всем организациям",
				"Пользователи 1С без ограничения по организациям",
				False,
			),
			(
				"IB Extra Roles",
				"Роли в обход профилей",
				"Роли пользователя 1С, которых нет в его профилях",
				False,
			),
			(
				"IB Login Not Working",
				"Вход в 1С у неработающих",
				"Вход разрешён, а сотрудник по кадрам не работает",
				False,
			),
			(
				"IB Users Without Employee",
				"Пользователи 1С без сотрудника",
				"Активные пользователи, для которых не найден сотрудник",
				False,
			),
			("IB Orphans", "Сироты ИБ", "Пользователи информационной базы без пользователя 1С", False),
		],
	),
	(
		"Active Directory",
		[
			(
				"AD Dismissed Enabled",
				"Включены у уволенных",
				"Учётка AD включена, а сотрудник не работает",
				False,
			),
			(
				"AD Without Employee",
				"Учётки AD без сотрудника",
				"Включённые учётки, владелец не найден",
				False,
			),
			(
				"AD Inactive Accounts",
				"Давно не входили в AD",
				"Включённые учётки без входа дольше заданного срока",
				False,
			),
			("AD Password Never Expires", "Пароль не истекает", "Учётки с бессрочным паролем", False),
			("AD Group Members", "Участники группы AD", "Кто входит в группу, включая вложенные", False),
			(
				"AD Disabled But 1C Active",
				"Отключены в AD, вход в 1С есть",
				"Учётка AD отключена, а в 1С вход разрешён",
				False,
			),
			(
				"AD employeeNumber",
				"Проставить employeeNumber",
				"Учётки, где employeeNumber не совпадает с сотрудником",
				False,
			),
		],
	),
	(
		"Битрикс24",
		[
			(
				"B24 Section Access",
				"Доступ к разделам Битрикс24",
				"Кто имеет доступ к CRM, смарт-процессам, папкам диска",
				False,
			),
			(
				"B24 Active Not Working",
				"Активны у неработающих",
				"Пользователь портала активен, а сотрудник не работает",
				False,
			),
			(
				"B24 Users Without Employee",
				"Пользователи без сотрудника",
				"Сотрудники портала без пары в кадрах",
				False,
			),
			(
				"B24 Workgroup Members",
				"Участники групп и проектов",
				"Участники с ролью и статусом сотрудника",
				False,
			),
			(
				"B24 Profile Differences",
				"Профиль не как в кадрах",
				"Отчество, дата рождения, должность, подразделение",
				True,
			),
			(
				"B24 Department Heads",
				"Руководители: Битрикс24 и кадры",
				"Руководитель подразделения на портале и в кадрах",
				False,
			),
			("B24 Structure", "Структура: расхождения", "Подразделения без пары", False),
			(
				"B24 Absence Differences",
				"Отсутствия: расхождения",
				"Отпуск или болезнь есть в ЗУП, но нет на портале, и наоборот",
				True,
			),
		],
	),
	(
		"Общие папки",
		[
			(
				"Share Access",
				"Кто имеет доступ к папкам",
				"Сотрудник, папка, уровень доступа и через какую группу",
				False,
			),
			(
				"Share Permission Issues",
				"Замечания по правам папок",
				"Прямые права, доступ для всех, запреты, удалённые учётки",
				False,
			),
		],
	),
	(
		"Ролевая модель и процессы",
		[
			(
				"Access Reconciliation",
				"Сверка доступа",
				"Положено по ролям и процессам и что есть в системах",
				False,
			),
			("Access Role Members", "Состав ролей", "Кто получает роль доступа и почему", False),
			("SoD Conflicts", "Конфликты полномочий", "Права, которые нельзя совмещать", False),
			(
				"Unmanaged Access",
				"Доступы вне каталога",
				"Выданные доступы, которых нет в каталоге прав",
				False,
			),
			("Role Mining", "Подбор ролей", "Предложения ролей по уже выданным доступам", False),
			(
				"Access Review Results",
				"Результаты пересмотра",
				"Решения проверяющих: оставить или отозвать",
				False,
			),
			("Process Participants", "Участники процессов", "Роли процессов и кто их исполняет", False),
			(
				"Process Continuity",
				"Риски процессов",
				"Роли без участников, без заместителя, с неработающими",
				False,
			),
		],
	),
]
BY_NAME = {
	name: (group, title, text, personal) for group, items in CATALOG for name, title, text, personal in items
}
LINK_OPTION_LIMIT = 1000
# link filters that the app renders as a person search instead of a list
PERSON_LINKS = {"Person"}


def report_module_path(name: str) -> str:
	report = frappe.get_cached_doc("Report", name)
	module_folder = frappe.scrub(report.module)
	app = frappe.local.module_app.get(module_folder, "access_registry")
	return os.path.join(
		frappe.get_app_path(app, module_folder), "report", frappe.scrub(name), frappe.scrub(name) + ".js"
	)


def _literal(text: str):
	text = text.strip()
	m = (
		re.fullmatch(r'__\(\s*"([^"]*)"\s*\)', text)
		or re.fullmatch(r'"([^"]*)"', text)
		or re.fullmatch(r"'([^']*)'", text)
	)
	if m:
		return m.group(1)
	if re.fullmatch(r"-?\d+", text):
		return int(text)
	if text.startswith("["):
		try:
			return json.loads(text.replace("'", '"'))
		except ValueError:
			return None
	return None


def _objects(source: str):
	"""Top-level {...} objects of the filters array."""
	depth, start = 0, None
	for i, ch in enumerate(source):
		if ch == "{":
			if depth == 0:
				start = i
			depth += 1
		elif ch == "}":
			depth -= 1
			if depth == 0 and start is not None:
				yield source[start + 1 : i]


def _props(body: str) -> dict:
	props = {}
	# key: value pairs; values are literals, __("…"), arrays or expressions (skipped)
	for m in re.finditer(r'(\w+)\s*:\s*(__\("[^"]*"\)|"[^"]*"|\'[^\']*\'|\[[^\]]*\]|-?\d+|[^,\n}]+)', body):
		props[m.group(1)] = _literal(m.group(2))
	return props


def parse_filters(name: str) -> list[dict]:
	path = report_module_path(name)
	if not os.path.exists(path):
		return []
	source = open(path, encoding="utf-8").read()
	m = re.search(r"filters\s*:\s*\[", source)
	if not m:
		return []
	depth, i = 1, m.end()
	while depth and i < len(source):
		depth += {"[": 1, "]": -1}.get(source[i], 0)
		i += 1
	filters = []
	for body in _objects(source[m.end() : i - 1]):
		p = _props(body)
		if not p.get("fieldname"):
			continue
		options = p.get("options")
		if isinstance(options, str) and "\n" in options:
			options = options.split("\n")
		filters.append(
			{
				"fieldname": p["fieldname"],
				"label": p.get("label") or p["fieldname"],
				"fieldtype": p.get("fieldtype") or "Data",
				"options": options,
				"default": p.get("default"),
				"reqd": bool(p.get("reqd")),
			}
		)
	return filters


def link_options(doctype: str) -> list[dict] | None:
	"""Values of a small Link filter as a list (title + value); None for big tables."""
	if doctype in PERSON_LINKS or not frappe.db.exists("DocType", doctype):
		return None
	if frappe.db.count(doctype) > LINK_OPTION_LIMIT:
		return None
	meta = frappe.get_meta(doctype)
	title = meta.title_field if meta.title_field and meta.title_field != "name" else None
	fields = ["name"] + ([title] if title else [])
	rows = frappe.get_all(
		doctype, fields=fields, order_by=f"{title or 'name'} asc", limit_page_length=LINK_OPTION_LIMIT
	)
	return [{"value": r.name, "label": (r.get(title) if title else None) or r.name} for r in rows]


def describe(name: str) -> dict:
	group, title, text, personal = BY_NAME[name]
	filters = parse_filters(name)
	for f in filters:
		if f["fieldtype"] == "Link":
			f["link_options"] = link_options(f["options"])
	return {
		"name": name,
		"group": group,
		"title": title,
		"description": text,
		"personal": personal,
		"filters": filters,
	}


COLUMN_TYPES = {
	"Int": "number",
	"Float": "number",
	"Currency": "number",
	"Percent": "number",
	"Date": "date",
	"Datetime": "datetime",
	"Check": "check",
}
APP_LINKS = {
	"Person": "person",
	"Entitlement": "entitlement",
	"Access Role": "role",
	"Business Process": "process",
}


def run(name: str, filters: dict) -> dict:
	from frappe.desk.query_report import generate_report_result

	report = frappe.get_doc("Report", name)
	clean = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
	# who may run the report is decided by app_access before the call; the desk row filters of
	# generate_report_result would need desk read rights on every linked DocType, which a user with
	# an access profile only (no desk roles) does not have
	result = generate_report_result(report, clean, user="Administrator")
	columns = []
	for c in result.get("columns") or []:
		if c.get("hidden"):
			continue
		columns.append(
			{
				"key": c.get("fieldname"),
				"label": _(c.get("label") or c.get("fieldname")),
				"type": "link"
				if c.get("fieldtype") == "Link"
				else COLUMN_TYPES.get(c.get("fieldtype"), "text"),
				"doctype": c.get("options") if c.get("fieldtype") == "Link" else None,
				"app_link": APP_LINKS.get(c.get("options")) if c.get("fieldtype") == "Link" else None,
				"width": cint(c.get("width")) or None,
			}
		)
	rows = []
	for r in result.get("result") or []:
		if isinstance(r, dict):
			rows.append({c["key"]: _plain(r.get(c["key"])) for c in columns})
	return {"columns": columns, "rows": rows, "message": result.get("message")}


def _plain(value):
	if value is None or isinstance(value, int | float | str):
		return value
	return str(value)


def to_xlsx(name: str, data: dict) -> bytes:
	from frappe.utils.xlsxutils import make_xlsx

	sheet = [[c["label"] for c in data["columns"]]]
	for r in data["rows"]:
		sheet.append([r.get(c["key"]) for c in data["columns"]])
	return make_xlsx(sheet, BY_NAME[name][1][:31]).getvalue()
