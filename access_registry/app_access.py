"""Access to the sections of the registry app /registry.

What a user sees comes from two sources, and the highest level wins:
- Frappe roles of the registry (as before): an administrator gets everything, an auditor reads
  everything and works in «Контроль», and so on (ROLE_ACCESS below);
- access profiles («Registry Access Profile»): a set of sections with levels and the users it is
  given to. A profile needs no desk role, so the app can be opened by people without a desk licence.

Levels: 0 — no access, 1 — «Просмотр», 2 — «Работа» (acting inside the section: suppress alerts,
mark HR events, agree exceptions).
"""

import frappe
from frappe import _

from access_registry.permissions import ADMINS, AUDITOR, PROCESS_MANAGER, REVIEWER, ROLE_MANAGER, VIEWER

NONE, VIEW, WORK = 0, 1, 2
LEVEL_OF = {"": NONE, None: NONE, "Просмотр": VIEW, "Работа": WORK}

# section code → title (the order of the app menu)
SECTIONS = {
	"overview": "Обзор",
	"management": "Руководству",
	"support": "Техподдержка",
	"people": "Сотрудники",
	"control": "Контроль",
	"access": "Права доступа",
	"roles": "Роли доступа",
	"processes": "Бизнес-процессы",
	"reviews": "Пересмотр доступа: кампании",
	"sources": "Источники",
	"reports": "Отчёты",
}
# sections with a working level; the others are only seen or not
WORK_SECTIONS = {"control", "roles"}

CONTROLS = {
	"dismissed": "Доступ у неработающих",
	"unlinked": "Учётки без сотрудника",
	"excess": "Лишние доступы",
	"missing": "Не хватает доступов",
	"sod": "Конфликты полномочий",
	"privileged": "Привилегированный доступ",
	"exceptions": "Исключения и временные роли",
	"stale": "Давно не входили",
	"processes": "Риски процессов",
	"quality": "Расхождения данных",
	"events": "Кадровые события: что сделать",
	"shares": "Общие папки: замечания",
	"assets": "Техника: замечания",
	"journal": "Журнал гашений",
}
CONTROL_BY_TITLE = {title: code for code, title in CONTROLS.items()}

# systems whose data a profile may be limited to (accounts, rights, alerts, reports); none — all
SYSTEMS = {
	"1c": "1С",
	"ad": "Active Directory",
	"b24": "Битрикс24",
	"shares": "Общие папки",
	"assets": "Техника",
}
# how the systems are written in rows of lists and reports
SYSTEM_OF_LABEL = {
	"1С": "1c",
	"AD": "ad",
	"Active Directory": "ad",
	"Битрикс24": "b24",
	"Общие папки": "shares",
	"Общие папки Synology": "shares",
	"Техника": "assets",
	"Snipe-IT": "assets",
}
# control lists that belong to one system
CONTROL_SYSTEM = {"shares": "shares", "assets": "assets"}

ALL_VIEW = {s: VIEW for s in SECTIONS}
# what the registry roles give (unchanged behaviour of the roles)
ROLE_ACCESS = {
	"admin": ({s: WORK if s in WORK_SECTIONS else VIEW for s in SECTIONS}, True),
	AUDITOR: ({**ALL_VIEW, "control": WORK}, True),
	ROLE_MANAGER: ({**ALL_VIEW, "control": WORK, "roles": WORK}, False),
	PROCESS_MANAGER: (ALL_VIEW, False),
	VIEWER: (ALL_VIEW, False),
}


def _empty():
	return {
		"sections": dict.fromkeys(SECTIONS, NONE),
		"personal": False,
		"control_lists": set(),
		"all_lists": False,
		"report_lists": set(),
		"all_reports": False,
		"systems": set(),
		"all_systems": False,
		"via": [],
	}


def _merge(result, sections, personal, lists=None, via=None, reports=None, systems=None):
	"""systems: None or empty — every system (roles of the registry, profiles without a choice)."""
	if any(sections.values()):
		if systems:
			result["systems"] |= set(systems)
		else:
			result["all_systems"] = True
	for section, level in sections.items():
		result["sections"][section] = max(result["sections"][section], level)
	result["personal"] = result["personal"] or personal
	if sections.get("control"):
		if lists:
			result["control_lists"] |= set(lists)
		else:
			result["all_lists"] = True
	if sections.get("reports"):
		if reports:
			result["report_lists"] |= set(reports)
		else:
			result["all_reports"] = True
	if via:
		result["via"].append(via)


def profiles_of(user: str) -> list:
	names = frappe.get_all(
		"Registry Access Member",
		filters={"parenttype": "Registry Access Profile", "user": user},
		pluck="parent",
	)
	if not names:
		return []
	return [p for p in (frappe.get_cached_doc("Registry Access Profile", n) for n in set(names)) if p.enabled]


def profile_access(profile) -> tuple[dict, bool, list]:
	sections = {
		"overview": VIEW if profile.s_overview else NONE,
		"management": VIEW if profile.get("s_management") else NONE,
		"support": VIEW if profile.get("s_support") else NONE,
		"people": VIEW if profile.s_people else NONE,
		"control": LEVEL_OF.get(profile.s_control, NONE),
		"access": VIEW if profile.s_access else NONE,
		"roles": LEVEL_OF.get(profile.s_roles, NONE),
		"processes": VIEW if profile.s_processes else NONE,
		"reviews": VIEW if profile.s_reviews else NONE,
		"sources": VIEW if profile.s_sources else NONE,
		"reports": VIEW if profile.get("s_reports") else NONE,
	}
	lists = [
		CONTROL_BY_TITLE[r.control_list] for r in profile.control_lists if r.control_list in CONTROL_BY_TITLE
	]
	return sections, bool(profile.s_personal), lists


def profile_systems(profile) -> list[str]:
	return [code for code in SYSTEMS if profile.get(f"sys_{code}")]


def profile_reports(profile) -> list[str]:
	return [r.report for r in profile.get("report_lists") or [] if r.report]


def compute(user: str | None = None) -> dict:
	user = user or frappe.session.user
	result = _empty()
	if user == "Guest":
		return result
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or roles & set(ADMINS):
		sections, personal = ROLE_ACCESS["admin"]
		_merge(result, sections, personal, via=_("администратор"))
		result["admin"] = True
		return result
	for role, (sections, personal) in ROLE_ACCESS.items():
		if role in roles:
			_merge(result, sections, personal, via=_("роль «{0}»").format(role))
	for profile in profiles_of(user):
		sections, personal, lists = profile_access(profile)
		_merge(
			result,
			sections,
			personal,
			lists,
			via=_("профиль «{0}»").format(profile.profile_name),
			reports=profile_reports(profile),
			systems=profile_systems(profile),
		)
	result["reviewer"] = REVIEWER in roles
	return result


def access(user: str | None = None) -> dict:
	"""The access of the user (a couple of queries; not cached, so role changes apply at once)."""
	return compute(user)


def level(section: str, user: str | None = None) -> int:
	return access(user)["sections"].get(section, NONE)


def has_section(section: str, need: int = VIEW) -> bool:
	return level(section) >= need


def any_section(user: str | None = None) -> bool:
	return any(access(user)["sections"].values())


def require_section(section: str, need: int = VIEW):
	if not has_section(section, need):
		raise frappe.PermissionError(
			_("Нет доступа к разделу «{0}»{1}").format(
				SECTIONS[section], _(" (нужен уровень «Работа»)") if need > VIEW else ""
			)
		)


def control_lists(user: str | None = None) -> list[str]:
	"""Control lists the user may open (in the order of CONTROLS)."""
	a = access(user)
	if not a["sections"]["control"]:
		return []
	allowed = systems_of(a)
	return [
		k
		for k in CONTROLS
		if (a["all_lists"] or k in a["control_lists"])
		and (k not in CONTROL_SYSTEM or CONTROL_SYSTEM[k] in allowed)
	]


# --------------------------------------------------------------------------- systems


def systems_of(a: dict) -> list[str]:
	return (
		list(SYSTEMS)
		if a.get("all_systems") or not a.get("systems")
		else [code for code in SYSTEMS if code in a["systems"]]
	)


def systems(user: str | None = None) -> list[str]:
	"""Systems whose data the user sees (codes of SYSTEMS)."""
	return systems_of(access(user))


def all_systems(user: str | None = None) -> bool:
	return len(systems(user)) == len(SYSTEMS)


def system_allowed(label, allowed: list[str] | None = None) -> bool:
	"""A row of a list or report with this system («1С», «AD», «Битрикс24»…) may be shown.

	Rows without a system are shown; «Другое» (systems the registry does not read) only to those
	who see every system."""
	allowed = systems() if allowed is None else allowed
	if not label:
		return True
	code = SYSTEM_OF_LABEL.get(str(label).strip())
	return code in allowed if code else len(allowed) == len(SYSTEMS)


def filter_rows(rows: list, key: str = "system", allowed: list[str] | None = None) -> list:
	allowed = systems() if allowed is None else allowed
	if len(allowed) == len(SYSTEMS):
		return rows
	return [r for r in rows if system_allowed(r.get(key), allowed)]


def require_control(kind: str, need: int = VIEW):
	if kind not in CONTROLS:
		frappe.throw(_("Неизвестный раздел контроля"))
	require_section("control", need)
	if kind not in control_lists():
		raise frappe.PermissionError(_("Нет доступа к списку «{0}»").format(CONTROLS.get(kind, kind)))


def personal() -> bool:
	return access()["personal"]


def can_open_app(user: str | None = None) -> bool:
	a = access(user)
	return any(a["sections"].values()) or bool(a.get("reviewer"))


def report_names(user: str | None = None) -> list[str]:
	"""Reports of the catalog the user may run in the app (personal-data reports need that flag)."""
	from access_registry.registry.reports import CATALOG, GROUP_SYSTEM

	a = access(user)
	if not a["sections"].get("reports"):
		return []
	allowed = systems_of(a)
	names = []
	for group, items in CATALOG:
		system = GROUP_SYSTEM.get(group)  # mixed groups (role model) are filtered by rows instead
		if system and system not in allowed:
			continue
		for name, _title, _text, needs_personal in items:
			if needs_personal and not a["personal"]:
				continue
			if a["all_reports"] or name in a["report_lists"]:
				names.append(name)
	return names


def require_report(name: str):
	require_section("reports")
	if name not in report_names():
		raise frappe.PermissionError(_("Нет доступа к отчёту «{0}»").format(name))
