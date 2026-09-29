"""Business processes to and from Excel: one workbook with three sheets.

«Процессы», «Роли», «Участники» — the same layout for the template, the export and the import,
so the usual way of working is: download, edit in Excel, load back. Loading is all-or-nothing:
if any row has an error, nothing is saved and every error is listed with its sheet and row.
"""

import io
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, getdate

from access_registry.access_catalog.linking import NameMatcher
from access_registry.sync.normalize import normalize_name

LEVELS = ("Группа процессов", "Процесс", "Подпроцесс")
STATUSES = ("Действует", "Черновик", "Архив")
RACI = {
	"R": "Исполнитель (R)",
	"A": "Отвечает за результат (A)",
	"C": "Консультант (C)",
	"I": "Информируется (I)",
}
PARTICIPATION = ("Основной", "Заместитель")
YES = {"да", "yes", "1", "true", "истина", "+", "y", "д"}
REQUIRED_MARK = "*"

PROCESS_COLUMNS = [
	("process_code", "Код"),
	("title", "Процесс*"),
	("parent", "Входит в (код или название)"),
	("level", "Уровень"),
	("status", "Статус"),
	("owner", "Владелец (ФИО)"),
	("version", "Версия"),
	("effective_from", "Действует с"),
	("goal", "Цель и результат"),
	("trigger_event", "Начинается с"),
	("systems", "Системы"),
	("regulation_url", "Регламент (ссылка)"),
	("description", "Описание"),
]
ROLE_COLUMNS = [
	("process", "Процесс* (код или название)"),
	("role_name", "Роль*"),
	("raci", "Участие (R/A/C/I)"),
	("description", "Обязанности"),
	("min_participants", "Нужно участников"),
	("needs_deputy", "Нужен заместитель (да/нет)"),
	("access_role", "Участники — все с ролью доступа"),
	("entitlements", "Нужные права (через ;)"),
]
PARTICIPANT_COLUMNS = [
	("process", "Процесс* (код или название)"),
	("role_name", "Роль*"),
	("person", "Сотрудник* (ФИО)"),
	("participation", "Участие (Основной/Заместитель)"),
	("valid_from", "С"),
	("valid_to", "По"),
	("note", "Комментарий"),
]
SHEETS = {
	"Процессы": PROCESS_COLUMNS,
	"Роли": ROLE_COLUMNS,
	"Участники": PARTICIPANT_COLUMNS,
}
HELP = [
	"Как заполнять",
	"",
	"Лист «Процессы»: одна строка — один процесс. Процесс узнаётся по коду, а если кода нет — по названию.",
	"«Входит в» — код или название процесса выше по иерархии (он может быть в этом же файле).",
	"Уровень: " + ", ".join(LEVELS) + ". Статус: " + ", ".join(STATUSES) + ".",
	"Владелец и сотрудники — ФИО как в кадрах. Однофамильцев различайте по UUID сотрудника из реестра.",
	"",
	"Лист «Роли»: роль процесса узнаётся по процессу и названию роли.",
	"Участие: R — исполнитель, A — отвечает за результат, C — консультант, I — информируется.",
	"«Участники — все с ролью доступа»: название роли доступа из реестра, её держатели станут участниками.",
	"«Нужные права»: названия прав из каталога прав доступа через точку с запятой.",
	"",
	"Лист «Участники»: сотрудник в роли процесса. Участие: Основной или Заместитель. Даты — необязательно.",
	"",
	"Пустая ячейка ничего не меняет у существующей записи. Поля со звёздочкой обязательны.",
	"Если в файле есть ошибки, не загружается ничего: исправьте строки из списка ошибок и загрузите снова.",
]


# --------------------------------------------------------------------------- export / template


def build_workbook(with_data: bool = True) -> bytes:
	"""The template: current processes, roles and participants (or examples when there are none)."""
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Font, PatternFill
	from openpyxl.worksheet.datavalidation import DataValidation

	wb = Workbook()
	help_sheet = wb.active
	help_sheet.title = "Как заполнять"
	for row, text in enumerate(HELP, start=1):
		help_sheet.cell(row=row, column=1, value=text)
	help_sheet["A1"].font = Font(bold=True, size=13)
	help_sheet.column_dimensions["A"].width = 120

	rows = export_rows() if with_data else {}
	if not any(rows.values()):
		rows = EXAMPLES
	head_fill = PatternFill("solid", fgColor="EEF2FF")
	for title, columns in SHEETS.items():
		ws = wb.create_sheet(title)
		for col, (_key, label) in enumerate(columns, start=1):
			cell = ws.cell(row=1, column=col, value=label)
			cell.font = Font(bold=True)
			cell.fill = head_fill
			cell.alignment = Alignment(wrap_text=True, vertical="top")
			ws.column_dimensions[cell.column_letter].width = 28 if col > 1 else 22
		for r, values in enumerate(rows.get(title, []), start=2):
			for col, (key, _label) in enumerate(columns, start=1):
				ws.cell(row=r, column=col, value=values.get(key))
		ws.freeze_panes = "A2"
		lists = {
			"level": LEVELS,
			"status": STATUSES,
			"raci": tuple(RACI),
			"needs_deputy": ("да", "нет"),
			"participation": PARTICIPATION,
		}
		for col, (key, _label) in enumerate(columns, start=1):
			if key in lists:
				letter = ws.cell(row=1, column=col).column_letter
				dv = DataValidation(type="list", formula1='"' + ",".join(lists[key]) + '"', allow_blank=True)
				dv.add(f"{letter}2:{letter}2000")
				ws.add_data_validation(dv)
	buffer = io.BytesIO()
	wb.save(buffer)
	return buffer.getvalue()


def export_rows() -> dict:
	names = dict(frappe.get_all("Person", fields=["name", "full_name"], as_list=True, limit_page_length=0))
	processes = frappe.get_all(
		"Business Process",
		fields=[
			"name",
			"process_code",
			"title",
			"parent_business_process",
			"level",
			"status",
			"owner_person",
			"version",
			"effective_from",
			"goal",
			"trigger_event",
			"systems",
			"regulation_url",
			"description",
		],
		order_by="lft",
		limit_page_length=0,
	)
	key_of = {p.name: p.process_code or p.title for p in processes}
	result = {"Процессы": [], "Роли": [], "Участники": []}
	for p in processes:
		result["Процессы"].append(
			{
				**p,
				"parent": key_of.get(p.parent_business_process),
				"owner": names.get(p.owner_person),
				"effective_from": getdate(p.effective_from) if p.effective_from else None,
			}
		)
	titles = dict(frappe.get_all("Entitlement", fields=["name", "title"], as_list=True, limit_page_length=0))
	entitlements = defaultdict(list)
	for row in frappe.get_all(
		"Process Role Entitlement",
		filters={"parenttype": "Process Role"},
		fields=["parent", "entitlement"],
		order_by="idx",
		limit_page_length=0,
	):
		entitlements[row.parent].append(titles.get(row.entitlement, row.entitlement))
	roles = frappe.get_all(
		"Process Role",
		fields=[
			"name",
			"business_process",
			"role_name",
			"raci",
			"description",
			"min_participants",
			"needs_deputy",
			"filled_by_access_role",
		],
		order_by="business_process, role_name",
		limit_page_length=0,
	)
	raci_code = {v: k for k, v in RACI.items()}
	role_of = {}
	for r in roles:
		role_of[r.name] = (key_of.get(r.business_process), r.role_name)
		result["Роли"].append(
			{
				"process": key_of.get(r.business_process),
				"role_name": r.role_name,
				"raci": raci_code.get(r.raci, r.raci),
				"description": r.description,
				"min_participants": r.min_participants,
				"needs_deputy": "да" if r.needs_deputy else "нет",
				"access_role": r.filled_by_access_role,
				"entitlements": "; ".join(entitlements.get(r.name, [])),
			}
		)
	for p in frappe.get_all(
		"Process Participant",
		fields=["process_role", "person", "participation", "valid_from", "valid_to", "note"],
		order_by="process_role, participation",
		limit_page_length=0,
	):
		process, role = role_of.get(p.process_role, (None, None))
		result["Участники"].append(
			{
				"process": process,
				"role_name": role,
				"person": names.get(p.person, p.person),
				"participation": p.participation,
				"valid_from": getdate(p.valid_from) if p.valid_from else None,
				"valid_to": getdate(p.valid_to) if p.valid_to else None,
				"note": p.note,
			}
		)
	return result


EXAMPLES = {
	"Процессы": [
		{"process_code": "ФИН", "title": "Финансы", "level": "Группа процессов", "status": "Действует"},
		{
			"process_code": "ФИН-01",
			"title": "Закрытие месяца",
			"parent": "ФИН",
			"level": "Процесс",
			"status": "Действует",
			"version": "1.0",
			"goal": "Отчётность сдана до 5 числа",
			"trigger_event": "Последний рабочий день месяца",
			"systems": "1С Бухгалтерия",
		},
	],
	"Роли": [
		{
			"process": "ФИН-01",
			"role_name": "Главный бухгалтер",
			"raci": "A",
			"min_participants": 1,
			"needs_deputy": "да",
		},
		{
			"process": "ФИН-01",
			"role_name": "Бухгалтер-исполнитель",
			"raci": "R",
			"min_participants": 2,
			"needs_deputy": "нет",
		},
	],
	"Участники": [],
}


# --------------------------------------------------------------------------- import


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


def _label_key(label) -> str:
	return normalize_name(str(label or "").replace(REQUIRED_MARK, "").split("(")[0])


def _clean(value):
	if isinstance(value, str):
		value = value.strip()
		return value or None
	return value


class ProcessImport:
	"""Validates the workbook against the registry and applies it. Errors → nothing is saved."""

	def __init__(self, content: bytes, remove_missing_participants: bool = False):
		self.sheets = read_workbook(content)
		self.remove_missing = remove_missing_participants
		self.errors: list[str] = []
		self.counters = defaultdict(int)
		self.names = NameMatcher()
		self.persons = set(frappe.get_all("Person", pluck="name", limit_page_length=0))
		self.processes = {}
		for p in frappe.get_all(
			"Business Process", fields=["name", "process_code", "title"], limit_page_length=0
		):
			if p.process_code:
				self.processes[normalize_name(p.process_code)] = p.name
			self.processes.setdefault(normalize_name(p.title), p.name)
		self.entitlements = defaultdict(list)
		for e in frappe.get_all("Entitlement", fields=["name", "title"], limit_page_length=0):
			self.entitlements[normalize_name(e.title)].append(e.name)
			self.entitlements[normalize_name(e.name)].append(e.name)
		self.access_roles = {normalize_name(r): r for r in frappe.get_all("Access Role", pluck="name")}

	def error(self, sheet, row, text):
		self.errors.append(_("Лист «{0}», строка {1}: {2}").format(sheet, row, text))

	def person(self, value, sheet, row, what):
		if not value:
			return None
		text = str(value).strip()
		if text.lower() in self.persons:
			return text.lower()
		person, note = self.names.match(text)
		if not person:
			self.error(sheet, row, _("{0}: {1}").format(what, note))
		return person

	def date(self, value, sheet, row, what):
		if value in (None, ""):
			return None
		try:
			return getdate(value)
		except Exception:
			self.error(sheet, row, _("{0}: не дата «{1}»").format(what, value))
			return None

	def run(self, apply: bool) -> dict:
		"""Checks everything; when ``apply`` and there are no errors, saves. Returns a summary."""
		savepoint = "process_import"
		frappe.db.savepoint(savepoint)
		try:
			self.import_processes()
			roles = self.import_roles()
			self.import_participants(roles)
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

	# ------------------------------------------------------------ processes

	def process_key(self, value):
		return self.processes.get(normalize_name(str(value))) if value not in (None, "") else None

	def import_processes(self):
		sheet = "Процессы"
		rows = self.sheets.get(sheet) or []
		pending = []
		for number, row in rows:
			if not row.get("title"):
				self.error(sheet, number, _("не указано название процесса"))
				continue
			if row.get("level") and row["level"] not in LEVELS:
				self.error(
					sheet,
					number,
					_("уровень «{0}» — нужно одно из: {1}").format(row["level"], ", ".join(LEVELS)),
				)
				continue
			if row.get("status") and row["status"] not in STATUSES:
				self.error(
					sheet,
					number,
					_("статус «{0}» — нужно одно из: {1}").format(row["status"], ", ".join(STATUSES)),
				)
				continue
			pending.append((number, row))
		# parents first: repeat while something was saved
		while pending:
			progress, rest = False, []
			for number, row in pending:
				parent_ref = row.get("parent")
				parent = self.process_key(parent_ref)
				in_file = parent_ref and any(
					normalize_name(str(parent_ref))
					in {normalize_name(str(r.get("process_code") or "")), normalize_name(str(r.get("title")))}
					for _n, r in pending
					if r is not row
				)
				if parent_ref and not parent and in_file:
					rest.append((number, row))
					continue
				if parent_ref and not parent:
					self.error(sheet, number, _("процесс «{0}» из «Входит в» не найден").format(parent_ref))
					continue
				self.save_process(number, row, parent)
				progress = True
			if not progress:
				for number, _row in rest:
					self.error(sheet, number, _("«Входит в» образует цикл или ссылается на строку с ошибкой"))
				break
			pending = rest

	def save_process(self, number, row, parent):
		sheet = "Процессы"
		name = self.process_key(row.get("process_code")) or self.process_key(row.get("title"))
		doc = frappe.get_doc("Business Process", name) if name else frappe.new_doc("Business Process")
		values = {
			"process_code": row.get("process_code"),
			"title": row.get("title"),
			"level": row.get("level"),
			"status": row.get("status"),
			"version": row.get("version"),
			"goal": row.get("goal"),
			"trigger_event": row.get("trigger_event"),
			"systems": row.get("systems"),
			"regulation_url": row.get("regulation_url"),
			"description": row.get("description"),
			"effective_from": self.date(row.get("effective_from"), sheet, number, _("«Действует с»")),
			"owner_person": self.person(row.get("owner"), sheet, number, _("владелец")),
		}
		if parent:
			values["parent_business_process"] = parent
		for field, value in values.items():
			if value not in (None, "") and str(doc.get(field) or "") != str(value):
				doc.set(field, str(value) if field == "version" else value)
		if parent and parent == doc.name:
			self.error(sheet, number, _("процесс не может входить сам в себя"))
			return
		new = doc.is_new()
		if new or _changed(doc):
			try:
				doc.save(ignore_permissions=False)
			except Exception as e:
				self.error(sheet, number, _cleanup(e))
				return
			self.counters["процессов создано" if new else "процессов изменено"] += 1
		for key in (row.get("process_code"), row.get("title")):
			if key:
				self.processes[normalize_name(str(key))] = doc.name

	# ------------------------------------------------------------ roles

	def import_roles(self) -> dict:
		sheet = "Роли"
		roles = {}
		for r in frappe.get_all(
			"Process Role", fields=["name", "business_process", "role_name"], limit_page_length=0
		):
			roles[(r.business_process, normalize_name(r.role_name))] = r.name
		for number, row in self.sheets.get(sheet) or []:
			process = self.process_key(row.get("process"))
			if not process:
				self.error(sheet, number, _("процесс «{0}» не найден").format(row.get("process") or ""))
				continue
			if not row.get("role_name"):
				self.error(sheet, number, _("не указано название роли"))
				continue
			raci = row.get("raci")
			if raci:
				code = str(raci).strip().upper()[:1]
				if code not in RACI and raci not in RACI.values():
					self.error(sheet, number, _("участие «{0}» — нужно R, A, C или I").format(raci))
					continue
				raci = RACI.get(code, raci)
			access_role = None
			if row.get("access_role"):
				access_role = self.access_roles.get(normalize_name(row["access_role"]))
				if not access_role:
					self.error(sheet, number, _("роль доступа «{0}» не найдена").format(row["access_role"]))
					continue
			entitlements = []
			for title in str(row.get("entitlements") or "").split(";"):
				title = title.strip()
				if not title:
					continue
				found = self.entitlements.get(normalize_name(title), [])
				if len(set(found)) != 1:
					self.error(
						sheet,
						number,
						_("право «{0}» {1}").format(
							title, _("не найдено в каталоге") if not found else _("неоднозначно")
						),
					)
					continue
				entitlements.append(found[0])
			key = (process, normalize_name(row["role_name"]))
			doc = (
				frappe.get_doc("Process Role", roles[key]) if key in roles else frappe.new_doc("Process Role")
			)
			doc.business_process = process
			doc.role_name = row["role_name"]
			if raci:
				doc.raci = raci
			if row.get("description"):
				doc.description = row["description"]
			if row.get("min_participants") not in (None, ""):
				doc.min_participants = cint(row["min_participants"])
			if row.get("needs_deputy") not in (None, ""):
				doc.needs_deputy = int(str(row["needs_deputy"]).strip().lower() in YES)
			if access_role:
				doc.filled_by_access_role = access_role
			if entitlements:
				present = {e.entitlement for e in doc.entitlements}
				for entitlement in entitlements:
					if entitlement not in present:
						doc.append("entitlements", {"entitlement": entitlement, "requirement": "Обязательно"})
			new = doc.is_new()
			if new or _changed(doc):
				try:
					doc.save(ignore_permissions=False)
				except Exception as e:
					self.error(sheet, number, _cleanup(e))
					continue
				self.counters["ролей создано" if new else "ролей изменено"] += 1
			roles[key] = doc.name
		return roles

	# ------------------------------------------------------------ participants

	def import_participants(self, roles: dict):
		sheet = "Участники"
		existing = {
			(p.process_role, p.person): p.name
			for p in frappe.get_all(
				"Process Participant", fields=["name", "process_role", "person"], limit_page_length=0
			)
		}
		seen = set()
		for number, row in self.sheets.get(sheet) or []:
			process = self.process_key(row.get("process"))
			if not process:
				self.error(sheet, number, _("процесс «{0}» не найден").format(row.get("process") or ""))
				continue
			role = roles.get((process, normalize_name(row.get("role_name") or "")))
			if not role:
				self.error(
					sheet,
					number,
					_("в процессе нет роли «{0}»: добавьте её на лист «Роли»").format(
						row.get("role_name") or ""
					),
				)
				continue
			if not row.get("person"):
				self.error(sheet, number, _("не указан сотрудник"))
				continue
			person = self.person(row["person"], sheet, number, _("сотрудник"))
			if not person:
				continue
			participation = row.get("participation") or "Основной"
			if participation not in PARTICIPATION:
				self.error(
					sheet,
					number,
					_("участие «{0}» — нужно «Основной» или «Заместитель»").format(participation),
				)
				continue
			valid_from = self.date(row.get("valid_from"), sheet, number, _("«С»"))
			valid_to = self.date(row.get("valid_to"), sheet, number, _("«По»"))
			seen.add((role, person))
			name = existing.get((role, person))
			doc = (
				frappe.get_doc("Process Participant", name) if name else frappe.new_doc("Process Participant")
			)
			doc.process_role = role
			doc.person = person
			doc.participation = participation
			for field, value in (
				("valid_from", valid_from),
				("valid_to", valid_to),
				("note", row.get("note")),
			):
				if value not in (None, ""):
					doc.set(field, value)
			new = doc.is_new()
			if new or _changed(doc):
				try:
					doc.save(ignore_permissions=False)
				except Exception as e:
					self.error(sheet, number, _cleanup(e))
					continue
				self.counters["участников добавлено" if new else "участников изменено"] += 1
			existing[(role, person)] = doc.name
		if self.remove_missing:
			# only in processes that appear on the sheet: other processes are left as they are
			touched = {self.process_key(r.get("process")) for _n, r in self.sheets.get(sheet) or []}
			touched_roles = {role for (process, _name), role in roles.items() if process in touched}
			for (role, person), name in list(existing.items()):
				if role in touched_roles and (role, person) not in seen:
					frappe.delete_doc("Process Participant", name)
					self.counters["участников удалено"] += 1


def _changed(doc) -> bool:
	from frappe.core.doctype.version.version import get_diff

	if doc.is_new():
		return True
	return bool(get_diff(frappe.get_doc(doc.doctype, doc.name), doc))


def _cleanup(error) -> str:
	import re

	return re.sub(r"<[^>]+>", "", str(error)) or error.__class__.__name__
