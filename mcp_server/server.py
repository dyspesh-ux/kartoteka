"""MCP server «kartoteka»: read-only access to the access registry for agents.

An agent can explore people, accesses, roles, processes, control findings and reports,
and ask for a role suggestion for a given employee. Nothing here writes to the registry:
proposing roles is analysis, decisions stay with people (and with the app's own
permission checks).

Run (stdio):  python -m mcp_server.server
Environment:   KARTOTEKA_URL, KARTOTEKA_API_KEY, KARTOTEKA_API_SECRET
"""

try:  # mcp 1.x
	from mcp.server.fastmcp import FastMCP as _MCPServer
except ModuleNotFoundError:  # mcp 2.x: FastMCP renamed to MCPServer
	from mcp.server.mcpserver import MCPServer as _MCPServer

from mcp_server.client import FrappeClient

API = "access_registry.registry.api"

# Reconciliation statuses (access_roles/engine.py).
STATUS_MISSING = "Не хватает"
STATUS_EXCESS = "Лишнее"
STATUS_EXCESS_NOT_WORKING = "Лишнее: не работает"
STATUS_EXCEPTION = "Исключение"
STATUS_OK = "Соответствует"
HELD_STATUSES = (STATUS_OK, STATUS_EXCESS, STATUS_EXCESS_NOT_WORKING, STATUS_EXCEPTION)
EXPECTED_STATUSES = (STATUS_OK, STATUS_MISSING)
ROLE_ACTIVE = "Действует"

# A typical-access gap counts when at least this share of fellow holders has the entitlement.
TYPICAL_SHARE = 50
# How many entitlements to look up granting roles for (per suggest_roles call).
MAX_ROLE_LOOKUPS = 25

mcp = _MCPServer("kartoteka")

_client: FrappeClient | None = None


def client() -> FrappeClient:
	global _client
	if _client is None:
		_client = FrappeClient.from_env()
	return _client


@mcp.tool()
def whoami() -> dict:
	"""Who the MCP server acts as in the registry and which sections it sees. Call first to check connectivity."""
	return client().call(f"{API}.bootstrap")


@mcp.tool()
def search_people(
	query: str = "", organization: str = "", status: str = "", flag: str = "", limit: int = 100
) -> dict:
	"""Search employees by name substring. Flags: dismissed_access, no_access, extra_roles, b24_admin."""
	return client().call(
		f"{API}.people",
		query=query or None,
		organization=organization or None,
		status=status or None,
		flag=flag or None,
		limit=limit,
	)


@mcp.tool()
def get_person(person_id: str) -> dict:
	"""Full 360° card of one employee: employments, accounts (1C/AD/Bitrix24/Diadoc), shares, assets,
	current access roles with reasons, «положено/есть» reconciliation, SoD conflicts."""
	return client().call(f"{API}.person", name=person_id)


@mcp.tool()
def get_position_profile(title: str) -> dict:
	"""A position by exact title: holders, roles mapped to it by rules, typical access of holders.
	Use it to see what people in a position usually have (basis for role proposals)."""
	return client().call(f"{API}.position", title=title)


@mcp.tool()
def list_access_roles() -> dict:
	"""Catalog of access roles with kind, status (Действует/Черновик), members and entitlement counts."""
	return {"rows": client().call(f"{API}.roles")}


@mcp.tool()
def get_access_role(name: str) -> dict:
	"""One access role: rules (position/department/organization), entitlements, members with per-person fit."""
	return client().call(f"{API}.role", name=name)


@mcp.tool()
def list_entitlements(system: str = "") -> dict:
	"""Catalog of entitlements (grantable rights). Systems: 1С, Active Directory, Битрикс24, Общие папки, Техника, Диадок."""
	return {"rows": client().call(f"{API}.entitlements", system=system or None)}


@mcp.tool()
def get_diadoc_organizations() -> dict:
	"""Diadoc (EDO) subscriber organizations: last upload, freshness flag, user and link counters."""
	return {"rows": client().call("access_registry.registry.diadoc_api.diadoc_organizations")}


@mcp.tool()
def get_diadoc_users(organization: str) -> dict:
	"""Users of one Diadoc organization (exact name from get_diadoc_organizations):
	right flags (admin, sign, payments, …), blocked/missing marks, linked employee."""
	return {
		"rows": client().call("access_registry.registry.diadoc_api.diadoc_users", organization=organization)
	}


@mcp.tool()
def get_entitlement(name: str) -> dict:
	"""One entitlement: holders with evidence, access roles and process roles granting it, exceptions."""
	return client().call(f"{API}.entitlement", name=name)


@mcp.tool()
def list_processes() -> dict:
	"""Business processes with their process roles (RACI)."""
	return {"rows": client().call(f"{API}.processes")}


@mcp.tool()
def get_process(name: str) -> dict:
	"""One business process: participants and the entitlements its process roles require."""
	return client().call(f"{API}.process", name=name)


@mcp.tool()
def list_reports() -> dict:
	"""Reports the registry user may run, by groups (includes role mining and SoD reports)."""
	return {"groups": client().call(f"{API}.reports_catalog")}


@mcp.tool()
def run_report(report_name: str, filters: dict | None = None) -> dict:
	"""Run a report by name (see list_reports). Use for role mining, SoD conflicts, reconciliation."""
	return client().call(f"{API}.run_report", name=report_name, filters=filters or {})


@mcp.tool()
def list_control_findings(kind: str, show_suppressed: bool = False) -> dict:
	"""Rows of a control list. Kinds: dismissed, unlinked, excess, missing, sod, privileged,
	exceptions, stale, processes, quality, events, shares, assets, journal."""
	return client().call(f"{API}.control", kind=kind, show_suppressed=1 if show_suppressed else 0)


@mcp.tool()
def search_registry(query: str) -> dict:
	"""Global search across people, positions, departments, accounts, entitlements, roles, processes."""
	return {"rows": client().call(f"{API}.search", query=query)}


@mcp.tool()
def suggest_roles(person_id: str) -> dict:
	"""Propose access roles for an employee: what is missing per the role model, what fellow holders of
	the position typically have, and which existing roles grant each gap. Analysis only — assignment
	stays a human decision in the app."""
	registry = client()
	data = registry.call(f"{API}.person", name=person_id)
	info = data.get("person") or {}
	reconciliation = data.get("reconciliation") or []
	held = {r["entitlement"] for r in reconciliation if r.get("status") in HELD_STATUSES}
	expected = {r["entitlement"] for r in reconciliation if r.get("status") in EXPECTED_STATUSES}

	position = {}
	position_title = info.get("position")
	if position_title:
		position = registry.call(f"{API}.position", title=position_title)
	typical_gaps = [
		a
		for a in position.get("access") or []
		if a.get("entitlement") not in held | expected and (a.get("share") or 0) >= TYPICAL_SHARE
	]
	missing = [r for r in reconciliation if r.get("status") == STATUS_MISSING]
	excess = [r for r in reconciliation if r.get("status") in (STATUS_EXCESS, STATUS_EXCESS_NOT_WORKING)]

	gap_ids = []
	for row in missing + typical_gaps:
		if row["entitlement"] not in gap_ids:
			gap_ids.append(row["entitlement"])
	lookup_ids, skipped = gap_ids[:MAX_ROLE_LOOKUPS], max(0, len(gap_ids) - MAX_ROLE_LOOKUPS)
	roles_catalog = {r["name"]: r for r in registry.call(f"{API}.roles")}
	granted_by = {}
	for entitlement_id in lookup_ids:
		detail = registry.call(f"{API}.entitlement", name=entitlement_id)
		granted_by[entitlement_id] = [
			{
				"role": row["role"],
				"requirement": row.get("requirement"),
				"status": (roles_catalog.get(row["role"]) or {}).get("status"),
			}
			for row in detail.get("roles") or []
		]

	suggestions = [
		{
			"kind": "missing",
			"entitlement": row["entitlement"],
			"title": row.get("title"),
			"system": row.get("system"),
			"expected_by": row.get("expected_by"),
			"granted_by": granted_by.get(row["entitlement"], []),
		}
		for row in missing
	] + [
		{
			"kind": "typical",
			"entitlement": a["entitlement"],
			"title": a.get("title"),
			"system": a.get("system"),
			"held_by_share_of_position_holders": a.get("share"),
			"granted_by": granted_by.get(a["entitlement"], []),
		}
		for a in typical_gaps
	]
	notes = [
		"kind=missing: право требуется ролевой моделью (expected_by — какая роль должна его давать).",
		"kind=typical: право не требуется моделью, но есть у большинства носителей должности.",
		"granted_by — все роли, дающие право; status «Действует» — кандидат на назначение, «Черновик» — сначала проверить.",
		"Назначение ролей и создание новых — только человеком в приложении; сервер ничего не записывает.",
	]
	if skipped:
		notes.append(f"Прав с поиском ролей больше лимита: {skipped} не разобраны.")
	if info.get("status") and info["status"] != "Работает":
		notes.append(f"Сотрудник в статусе «{info['status']}»: сначала разберитесь с кадровым статусом.")
	return {
		"person": info,
		"current_roles": data.get("roles") or [],
		"position": position_title,
		"position_roles": position.get("roles") or [],
		"missing": missing,
		"excess": excess,
		"sod_conflicts": data.get("sod") or [],
		"suggestions": suggestions,
		"notes": notes,
	}


if __name__ == "__main__":
	mcp.run()
