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
	"people": "Сотрудники",
	"control": "Контроль",
	"access": "Права доступа",
	"roles": "Роли доступа",
	"processes": "Бизнес-процессы",
	"reviews": "Пересмотр доступа: кампании",
	"sources": "Источники",
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
	"journal": "Журнал гашений",
}
CONTROL_BY_TITLE = {title: code for code, title in CONTROLS.items()}

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
		"via": [],
	}


def _merge(result, sections, personal, lists=None, via=None):
	for section, level in sections.items():
		result["sections"][section] = max(result["sections"][section], level)
	result["personal"] = result["personal"] or personal
	if sections.get("control"):
		if lists:
			result["control_lists"] |= set(lists)
		else:
			result["all_lists"] = True
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
		"people": VIEW if profile.s_people else NONE,
		"control": LEVEL_OF.get(profile.s_control, NONE),
		"access": VIEW if profile.s_access else NONE,
		"roles": LEVEL_OF.get(profile.s_roles, NONE),
		"processes": VIEW if profile.s_processes else NONE,
		"reviews": VIEW if profile.s_reviews else NONE,
		"sources": VIEW if profile.s_sources else NONE,
	}
	lists = [
		CONTROL_BY_TITLE[r.control_list] for r in profile.control_lists if r.control_list in CONTROL_BY_TITLE
	]
	return sections, bool(profile.s_personal), lists


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
		_merge(result, sections, personal, lists, via=_("профиль «{0}»").format(profile.profile_name))
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
	return [k for k in CONTROLS if a["all_lists"] or k in a["control_lists"]]


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
