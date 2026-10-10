"""Role model: who should have what (roles, process roles) and who actually has what (1C, AD, Bitrix24).

Terms (see docs/roles-and-processes.md):
- entitlement («право доступа») — one grantable thing in one system: a 1C profile, an AD group,
  a Bitrix24 workgroup or CRM role;
- access role («роль доступа») — a named set of entitlements. A job role gets people by rules
  over their employments (position, department, organization); a base role — every working
  employee; an additional role — only by a manual time-limited assignment;
- process role — a role in a business process; its participants need its entitlements too.

«Положено» = entitlements of the person's roles and process roles; «есть» = what the mirrors of 1C,
AD and Bitrix24 show. The difference is «не хватает» and «лишнее»; an approved exception turns
«лишнее» into «исключение».
"""

from collections import Counter, defaultdict

import frappe
from frappe.utils import getdate, today

from access_registry.sync.normalize import normalize_name

ACTIVE_EMPLOYMENT = ("Работает", "Увольняется")
MAIN_KIND = "ОсновноеМестоРаботы"
MANDATORY = "Обязательно"

MISSING = "Не хватает"
EXCESS = "Лишнее"
EXCESS_NOT_WORKING = "Лишнее: не работает"
EXCEPTION = "Исключение"
OK = "Соответствует"
STATUSES = (MISSING, EXCESS, EXCESS_NOT_WORKING, EXCEPTION, OK)

PRIVILEGED_WORDS = ("админ", "полные права", "full", "admin", "администратор")


def _active(valid_from, valid_to, on) -> bool:
	return (not valid_from or getdate(valid_from) <= on) and (not valid_to or getdate(valid_to) >= on)


# --------------------------------------------------------------------------- «положено»


def _scope(persons) -> list | None:
	"""Persons as a list for «in» filters; None — every person."""
	if persons is None:
		return None
	return [p for p in set(persons) if p] or [""]


class RoleModel:
	"""Roles and process roles of every working employee, and the entitlements they bring.

	persons: only these employees (a card of one person or of a role's members); the roles, process
	roles and their entitlements are loaded whole anyway."""

	def __init__(self, on=None, persons=None):
		self.on = getdate(on or today())
		only = _scope(persons)
		self.bounds = {
			d.name: (d.lft, d.rgt)
			for d in frappe.get_all("HR Department", fields=["name", "lft", "rgt"], limit_page_length=0)
		}
		self.persons = {
			p.name: p
			for p in frappe.get_all(
				"Person",
				filters={"name": ["in", only]} if only else None,
				fields=["name", "full_name", "status"],
				limit_page_length=0,
			)
		}
		self.employments = defaultdict(list)
		for e in frappe.db.sql(
			f"""select e.person, e.department, e.organization, e.employment_kind_code, pos.title as position_title
			from `tabEmployment` e left join `tabHR Position` pos on pos.name = e.position
			where e.status in %(active)s and ifnull(e.person, '') != ''
				{"and e.person in %(persons)s" if only else ""}""",
			{"active": ACTIVE_EMPLOYMENT, "persons": only},
			as_dict=True,
		):
			e.position_key = normalize_name(e.position_title)
			self.employments[e.person].append(e)

		self.roles = {}
		for role in frappe.get_all(
			"Access Role", filters={"status": "Действует"}, fields=["name", "kind"], limit_page_length=0
		):
			role.rules = []
			role.entitlements = []
			self.roles[role.name] = role
		for rule in frappe.get_all(
			"Access Role Rule",
			filters={"parenttype": "Access Role", "parent": ["in", list(self.roles) or [""]]},
			fields=[
				"parent",
				"position_title",
				"department",
				"include_subdepartments",
				"organization",
				"main_only",
			],
			limit_page_length=0,
		):
			rule.position_key = normalize_name(rule.position_title)
			self.roles[rule.parent].rules.append(rule)
		# job roles a rule of which may match a position (a rule without a position matches any):
		# the others are not checked rule by rule for every employee
		self.roles_by_position = defaultdict(set)
		self.roles_any_position = set()
		for role in self.roles.values():
			if role.kind == "Должностная":
				for rule in role.rules:
					if rule.position_key:
						self.roles_by_position[rule.position_key].add(role.name)
					else:
						self.roles_any_position.add(role.name)
		for row in frappe.get_all(
			"Access Role Entitlement",
			filters={"parenttype": "Access Role", "parent": ["in", list(self.roles) or [""]]},
			fields=["parent", "entitlement", "requirement"],
			limit_page_length=0,
		):
			self.roles[row.parent].entitlements.append(row)

		self.assignments = defaultdict(list)
		for a in frappe.get_all(
			"Access Role Assignment",
			filters={"person": ["in", only]} if only else None,
			fields=["person", "access_role", "valid_from", "valid_to", "reason"],
			limit_page_length=0,
		):
			if a.access_role in self.roles and _active(a.valid_from, a.valid_to, self.on):
				self.assignments[a.person].append(a)

		self.process_roles = {}
		for pr in frappe.db.sql(
			"""select r.name, r.role_name, r.raci, r.filled_by_access_role, r.business_process as process, p.title as process_title
			from `tabProcess Role` r join `tabBusiness Process` p on p.name = r.business_process
			where p.status = 'Действует'""",
			as_dict=True,
		):
			pr.entitlements = []
			self.process_roles[pr.name] = pr
		for row in frappe.get_all(
			"Process Role Entitlement",
			filters={"parenttype": "Process Role", "parent": ["in", list(self.process_roles) or [""]]},
			fields=["parent", "entitlement", "requirement"],
			limit_page_length=0,
		):
			self.process_roles[row.parent].entitlements.append(row)
		self.participants = defaultdict(list)
		for p in frappe.get_all(
			"Process Participant",
			filters={"person": ["in", only]} if only else None,
			fields=["person", "process_role", "participation", "valid_from", "valid_to"],
			limit_page_length=0,
		):
			if p.process_role in self.process_roles and _active(p.valid_from, p.valid_to, self.on):
				self.participants[p.person].append(p)
		# process roles filled by an access role (the others need no roles of the person)
		self.filled_process_roles = [pr for pr in self.process_roles.values() if pr.filled_by_access_role]
		self._roles_cache = {}

	# ------------------------------------------------------------ roles

	def rule_matches(self, rule, e) -> bool:
		if rule.position_key and rule.position_key != e.position_key:
			return False
		if rule.organization and rule.organization != e.organization:
			return False
		if rule.main_only and e.employment_kind_code != MAIN_KIND:
			return False
		if rule.department:
			if rule.include_subdepartments:
				outer, inner = self.bounds.get(rule.department), self.bounds.get(e.department)
				if not outer or not inner or not (outer[0] <= inner[0] and inner[1] <= outer[1]):
					return False
			elif rule.department != e.department:
				return False
		return True

	def working(self, person) -> bool:
		return bool(self.employments.get(person))

	def roles_of(self, person) -> dict:
		"""{access role: reason}. Only working employees get roles by rules; assignments count always."""
		if person in self._roles_cache:
			return self._roles_cache[person]
		result = {}
		employments = self.employments.get(person, [])
		if employments:
			candidates = self.roles_any_position.union(
				*(self.roles_by_position.get(e.position_key, ()) for e in employments)
			)
			for role in self.roles.values():
				if role.kind == "Базовая":
					result[role.name] = "всем работающим"
				elif role.kind == "Должностная" and role.name in candidates:
					for rule in role.rules:
						match = next((e for e in employments if self.rule_matches(rule, e)), None)
						if match:
							result[role.name] = "по должности: " + (match.position_title or "—")
							break
		for a in self.assignments.get(person, []):
			result.setdefault(
				a.access_role,
				"назначена: " + (a.reason or "").strip().splitlines()[0][:80] if a.reason else "назначена",
			)
		self._roles_cache[person] = result
		return result

	def process_roles_of(self, person) -> dict:
		"""{process role: reason}: manual participants and holders of the linked access role."""
		result = {}
		for p in self.participants.get(person, []):
			result[p.process_role] = p.participation or "Основной"
		if not self.filled_process_roles:
			return result
		roles = self.roles_of(person)
		for pr in self.filled_process_roles:
			if pr.filled_by_access_role in roles and pr.name not in result:
				result[pr.name] = f"по роли доступа «{pr.filled_by_access_role}»"
		return result

	def members_of_role(self, role: str) -> dict:
		return {p: reason for p in self.persons if (reason := self.roles_of(p).get(role))}

	def participants_of(self, process_role: str) -> dict:
		return {p: reason for p in self.persons if (reason := self.process_roles_of(p).get(process_role))}

	# ------------------------------------------------------------ entitlements

	def expected(self, person) -> dict:
		"""{entitlement: {"mandatory": bool, "reasons": [..]}}."""
		result = {}

		def add(entitlement, requirement, reason):
			item = result.setdefault(entitlement, {"mandatory": False, "reasons": []})
			item["mandatory"] = item["mandatory"] or (requirement or MANDATORY) == MANDATORY
			item["reasons"].append(reason)

		for role in self.roles_of(person):
			for row in self.roles[role].entitlements:
				add(row.entitlement, row.requirement, f"роль «{role}»")
		for name in self.process_roles_of(person):
			pr = self.process_roles[name]
			for row in pr.entitlements:
				add(row.entitlement, row.requirement, f"процесс «{pr.process_title}»: {pr.role_name}")
		return result


# --------------------------------------------------------------------------- «есть»


def raw_accesses(persons=None) -> dict:
	"""What each employee actually has in 1C, AD and Bitrix24, whether catalogued or not.

	{person: {key: evidence}}; keys: 1c:<IB Access Profile>, ad:<AD Group>, b24wg:<B24 Workgroup>,
	b24:<portal>|<via>|<resource or ''>, bit:<base>|<kind>|<name> (treasury rights of BIT.Finance:
	a visa, an executor role or access to a CFO).
	"""
	result = defaultdict(dict)
	# persons given: only their accounts are read (a card of one person must not read the whole
	# portal and domain); persons empty or None: everybody
	only = _scope(persons) if persons else None
	params = {"persons": only}
	of_persons = "and u.person in %(persons)s" if only else ""

	def add(person, key, evidence):
		if person:
			result[person].setdefault(key, evidence)

	for person, profile, base, login in frappe.db.sql(
		f"""select u.person, r.profile, u.base_code, ifnull(u.login, u.user_name)
		from `tabIB User Profile` r join `tabIB User` u on u.name = r.parent
		where u.login_allowed = 1 and u.invalid = 0 and u.missing_in_source = 0
			and ifnull(u.person, '') != '' and ifnull(r.profile, '') != '' {of_persons}""",
		params,
	):
		add(person, f"1c:{profile}", f"1С {base}: {login}")
	for person, base, kind, name, login in frappe.db.sql(
		f"""select u.person, u.base_code, b.kind, b.right_name, ifnull(u.login, u.user_name)
		from `tabIB User BIT Right` b join `tabIB User` u on u.name = b.parent
		where b.parenttype = 'IB User' and u.login_allowed = 1 and u.invalid = 0 and u.missing_in_source = 0
			and ifnull(u.person, '') != '' and ifnull(b.right_name, '') != '' {of_persons}""",
		params,
	):
		add(person, bit_key(base, kind, name), f"1С {base}: {login} (БИТ.Финанс)")
	from access_registry.active_directory.groups import effective_account_groups

	security = set(frappe.get_all("AD Group", filters={"security": 1}, pluck="name", limit_page_length=0))
	accounts = {
		a.name: a
		for a in frappe.get_all(
			"AD Account",
			filters={"enabled": 1, "missing_in_source": 0, "person": ["in", only] if only else ["is", "set"]},
			fields=["name", "person", "domain", "sam_account_name"],
			limit_page_length=0,
		)
	}
	if accounts:
		for account, groups in effective_account_groups(accounts=list(accounts) if only else None).items():
			a = accounts.get(account)
			if not a:
				continue
			for group in groups & security:  # nested groups count: access comes through them too
				add(a.person, f"ad:{group}", f"AD {a.domain}\\{a.sam_account_name}")
	for person, group, name in frappe.db.sql(
		f"""select u.person, m.parent, u.full_name
		from `tabB24 Workgroup Member` m join `tabB24 User` u on u.name = m.user
		where u.active = 1 and u.missing_in_source = 0 and ifnull(u.person, '') != '' {of_persons}""",
		params,
	):
		add(person, f"b24wg:{group}", f"Битрикс24: {name}")
	if frappe.db.count("B24 Access Grant"):
		from access_registry.bitrix24.access import effective_grants

		for portal in frappe.get_all("B24 Portal", pluck="name"):
			users = None
			if only:
				# grants of the portal expanded only to the users of these persons
				users = frappe.get_all(
					"B24 User",
					filters={"portal": portal, "person": ["in", only], "active": 1, "missing_in_source": 0},
					pluck="name",
				)
				if not users:
					continue
			for row in effective_grants(portal, users=users):
				if row["person"] and row["active"] and not row["negative"]:
					add(
						row["person"],
						b24_key(portal, row["via"], row["resource"]),
						f"Битрикс24: {row['user_name']}",
					)
	return result


def b24_key(portal, via, resource) -> str:
	"""Folder rights are told apart by the folder, CRM roles and user groups by the role or group."""
	via = via or ""
	return f"b24:{portal}|{via}|{resource if via == 'Права на папку' else ''}"


def bit_key(base: str, kind: str, name: str) -> str:
	return f"bit:{base}|{kind}|{name}"


def entitlement_keys() -> dict:
	"""{entitlement: [keys it covers]} for active catalogued entitlements."""
	keys = defaultdict(list)
	for e in frappe.get_all(
		"Entitlement",
		filters={"active": 1},
		fields=[
			"name",
			"system",
			"ib_profile",
			"ad_group",
			"b24_workgroup",
			"b24_portal",
			"b24_via",
			"b24_resource",
			"bit_kind",
			"bit_name",
			"bit_base",
		],
		limit_page_length=0,
	):
		if e.bit_kind and e.bit_name:
			bases = [e.bit_base] if e.bit_base else frappe.get_all("Info Base", pluck="name")
			for base in bases:
				keys[e.name].append(bit_key(base, e.bit_kind, e.bit_name.strip()))
		if e.ib_profile:
			keys[e.name].append(f"1c:{e.ib_profile}")
		if e.ad_group:
			keys[e.name].append(f"ad:{e.ad_group}")
		if e.b24_workgroup:
			keys[e.name].append(f"b24wg:{e.b24_workgroup}")
		if e.b24_via:
			portals = [e.b24_portal] if e.b24_portal else frappe.get_all("B24 Portal", pluck="name")
			for portal in portals:
				if e.b24_via == "Права на папку" or e.b24_resource:
					keys[e.name].append(f"b24:{portal}|{e.b24_via}|{e.b24_resource or ''}")
				else:
					keys[e.name].append(f"b24:{portal}|{e.b24_via}|")
	return keys


def actual_entitlements(persons=None) -> tuple[dict, dict]:
	"""({person: {entitlement: evidence}}, {person: {uncatalogued key: evidence}})."""
	raw = raw_accesses(persons)
	by_key = defaultdict(list)
	for entitlement, keys in entitlement_keys().items():
		for key in keys:
			by_key[key].append(entitlement)
	actual, other = defaultdict(dict), defaultdict(dict)
	for person, items in raw.items():
		for key, evidence in items.items():
			if key in by_key:
				for entitlement in by_key[key]:
					actual[person].setdefault(entitlement, evidence)
			else:
				other[person][key] = evidence
	return actual, other


# --------------------------------------------------------------------------- reconciliation


def active_exceptions(on=None) -> dict:
	on = getdate(on or today())
	result = defaultdict(dict)
	for e in frappe.get_all(
		"Access Exception",
		fields=["name", "person", "entitlement", "valid_to", "reason"],
		limit_page_length=0,
	):
		if not e.valid_to or getdate(e.valid_to) >= on:
			result[e.person][e.entitlement] = e
	return result


def reconcile(persons=None, model=None, actual=None) -> list[dict]:
	"""One row per (person, entitlement) that is expected or actually held.

	actual: actual_entitlements() already read for the same persons (the dashboard needs it twice)."""
	model = model or RoleModel(persons=persons or None)
	if actual is None:
		actual, _other = actual_entitlements(persons)
	exceptions = active_exceptions(model.on)
	entitlements = {
		e.name: e
		for e in frappe.get_all(
			"Entitlement", fields=["name", "title", "system", "risk", "privileged"], limit_page_length=0
		)
	}
	scope = persons or set(model.employments) | set(actual)
	rows = []
	for person in scope:
		info = model.persons.get(person)
		if not info:
			continue
		working = model.working(person)
		expected = model.expected(person) if working else {}
		held = actual.get(person, {})
		for entitlement in sorted(set(expected) | set(held)):
			meta = entitlements.get(entitlement)
			if not meta:
				continue
			want = expected.get(entitlement)
			has = entitlement in held
			exception = exceptions.get(person, {}).get(entitlement)
			if has and want:
				status = OK
			elif want and not has:
				if not want["mandatory"]:
					continue  # allowed, but not required
				status = MISSING
			elif not working:
				status = EXCESS_NOT_WORKING
			elif exception:
				status = EXCEPTION
			else:
				status = EXCESS
			rows.append(
				{
					"person": person,
					"full_name": info.full_name,
					"person_status": info.status,
					"entitlement": entitlement,
					"title": meta.title,
					"system": meta.system,
					"risk": meta.risk,
					"privileged": meta.privileged,
					"status": status,
					"expected_by": "; ".join(want["reasons"]) if want else "",
					"evidence": held.get(entitlement, ""),
					"exception": exception.name if exception else None,
				}
			)
	rows.sort(key=lambda r: (r["full_name"] or "", r["system"] or "", r["title"] or ""))
	return rows


def sod_conflicts(persons=None, actual=None) -> list[dict]:
	rules = frappe.get_all(
		"SoD Rule", filters={"active": 1}, fields=["name", "title", "severity", "description"]
	)
	if not rules:
		return []
	if actual is None:
		actual, _other = actual_entitlements(persons)
	elif persons:
		actual = {p: held for p, held in actual.items() if p in persons}
	titles = dict(frappe.get_all("Entitlement", fields=["name", "title"], as_list=True, limit_page_length=0))
	names = {
		p.name: p
		for p in frappe.get_all(
			"Person",
			filters={"name": ["in", list(actual) or [""]]} if persons else None,
			fields=["name", "full_name", "status"],
			limit_page_length=0,
		)
	}
	sides = defaultdict(lambda: defaultdict(set))
	for row in frappe.get_all(
		"SoD Rule Entitlement",
		filters={"parenttype": "SoD Rule"},
		fields=["parent", "parentfield", "entitlement"],
		limit_page_length=0,
	):
		sides[row.parent][row.parentfield].add(row.entitlement)
	rows = []
	for rule in rules:
		a, b = sides[rule.name]["side_a"], sides[rule.name]["side_b"]
		for person, held in actual.items():
			left, right = a & set(held), b & set(held)
			if left and right:
				rows.append(
					{
						"rule": rule.name,
						"title": rule.title,
						"severity": rule.severity,
						"person": person,
						"full_name": names.get(person, {}).get("full_name"),
						"person_status": names.get(person, {}).get("status"),
						"side_a": ", ".join(sorted(titles.get(x, x) for x in left)),
						"side_b": ", ".join(sorted(titles.get(x, x) for x in right)),
					}
				)
	rows.sort(key=lambda r: ({"Критичная": 0, "Высокая": 1}.get(r["severity"], 2), r["full_name"] or ""))
	return rows


def role_design_conflicts() -> list[dict]:
	"""Roles and process roles that by themselves grant both sides of a SoD rule."""
	sides = defaultdict(lambda: defaultdict(set))
	for row in frappe.get_all(
		"SoD Rule Entitlement",
		filters={"parenttype": "SoD Rule"},
		fields=["parent", "parentfield", "entitlement"],
	):
		sides[row.parent][row.parentfield].add(row.entitlement)
	holders = defaultdict(set)
	for row in frappe.get_all(
		"Access Role Entitlement", filters={"parenttype": "Access Role"}, fields=["parent", "entitlement"]
	):
		holders[("Access Role", row.parent)].add(row.entitlement)
	for row in frappe.get_all(
		"Process Role Entitlement", filters={"parenttype": "Process Role"}, fields=["parent", "entitlement"]
	):
		holders[("Process Role", row.parent)].add(row.entitlement)
	rows = []
	for rule, parts in sides.items():
		for (doctype, name), items in holders.items():
			if items & parts["side_a"] and items & parts["side_b"]:
				rows.append({"rule": rule, "doctype": doctype, "name": name})
	return rows


# --------------------------------------------------------------------------- catalog helpers


def describe_key(key: str) -> dict:
	"""Title, system and link fields of an entitlement for a raw access key."""
	kind, _, ref = key.partition(":")
	if kind == "bit":
		base, bit_kind, name = (ref.split("|", 2) + ["", ""])[:3]
		what = {"Виза": "виза", "Роль исполнителя": "роль", "Доступ к ЦФО": "доступ к ЦФО"}.get(
			bit_kind, bit_kind
		)
		return {
			"title": f"БИТ.Финанс {base}: {what} «{name}»",
			"system": "1С",
			"bit_base": base,
			"bit_kind": bit_kind,
			"bit_name": name,
		}
	if kind == "1c":
		profile = (
			frappe.db.get_value("IB Access Profile", ref, ["profile_name", "base_code"], as_dict=True) or {}
		)
		return {
			"title": f"1С {profile.get('base_code', '')}: {profile.get('profile_name') or ref}",
			"system": "1С",
			"ib_profile": ref,
		}
	if kind == "ad":
		group = frappe.db.get_value("AD Group", ref, ["group_name", "domain"], as_dict=True) or {}
		return {
			"title": f"AD {group.get('domain', '')}: {group.get('group_name') or ref}",
			"system": "Active Directory",
			"ad_group": ref,
		}
	if kind == "b24wg":
		group = frappe.db.get_value("B24 Workgroup", ref, ["group_name", "portal"], as_dict=True) or {}
		return {
			"title": f"Битрикс24: группа «{group.get('group_name') or ref}»",
			"system": "Битрикс24",
			"b24_workgroup": ref,
			"b24_portal": group.get("portal"),
		}
	portal, via, resource = (ref.split("|") + ["", ""])[:3]
	return {
		"title": f"Битрикс24: {via}" + (f" — {resource}" if resource else ""),
		"system": "Битрикс24",
		"b24_portal": portal,
		"b24_via": via,
		"b24_resource": resource or None,
	}


def find_entitlement(key: str) -> str | None:
	for entitlement, keys in entitlement_keys().items():
		if key in keys:
			return entitlement
	return None


def ensure_entitlement(key: str) -> str:
	existing = find_entitlement(key)
	if existing:
		return existing
	values = describe_key(key)
	privileged = any(word in values["title"].lower() for word in PRIVILEGED_WORDS)
	doc = frappe.get_doc(
		{
			"doctype": "Entitlement",
			**values,
			"risk": "Высокий" if privileged else "Средний",
			"privileged": int(privileged),
			"description": "Создано подбором ролей",
		}
	).insert(ignore_permissions=True)
	return doc.name


# --------------------------------------------------------------------------- role mining


def mine_roles(min_people: int = 3, threshold: float = 0.8) -> list[dict]:
	"""Initial reconciliation: for every position, which accesses most of its holders already have.

	One row per (position, access) held by at least one employee of the position; ``suggested`` —
	the share of holders reaches the threshold.
	"""
	from access_registry.access_catalog.access_report import main_places

	model = RoleModel()
	working = [p for p in model.employments]
	places = main_places(working)
	groups = defaultdict(list)
	titles = {}
	for person in working:
		title = (places.get(person) or {}).get("position_title")
		if title:
			key = normalize_name(title)
			groups[key].append(person)
			titles.setdefault(key, title)
	raw = raw_accesses(working)
	catalog = {}
	for entitlement, keys in entitlement_keys().items():
		for key in keys:
			catalog[key] = entitlement
	existing_roles = {
		normalize_name(r.position_title): r.parent
		for r in frappe.get_all(
			"Access Role Rule", filters={"parenttype": "Access Role"}, fields=["parent", "position_title"]
		)
		if r.position_title
	}
	rows = []
	for key, people in sorted(groups.items(), key=lambda kv: -len(kv[1])):
		if len(people) < min_people:
			continue
		counts = Counter()
		for person in people:
			counts.update(raw.get(person, {}).keys())
		for access, holders in counts.most_common():
			share = holders / len(people)
			rows.append(
				{
					"position": titles[key],
					"people": len(people),
					"access": access,
					"title": describe_key(access)["title"],
					"holders": holders,
					"share": round(share * 100, 1),
					"suggested": int(share >= threshold),
					"entitlement": catalog.get(access),
					"existing_role": existing_roles.get(key),
				}
			)
	return rows


def create_draft_roles(
	min_people: int = 3, threshold: float = 0.8, positions: list | None = None
) -> list[str]:
	"""Draft job roles from mine_roles(): one per position, entitlements with coverage ≥ threshold."""
	by_position = defaultdict(list)
	for row in mine_roles(min_people, threshold):
		if row["suggested"] and not row["existing_role"]:
			if positions and row["position"] not in positions:
				continue
			by_position[(row["position"], row["people"])].append(row)
	created = []
	for (position, people), items in by_position.items():
		name = f"Должность: {position}"
		if frappe.db.exists("Access Role", name):
			continue
		role = frappe.get_doc(
			{
				"doctype": "Access Role",
				"role_name": name,
				"kind": "Должностная",
				"status": "Черновик",
				"description": (
					f"Создано подбором ролей: права, которые уже есть не меньше чем у {int(threshold * 100)}% "
					f"из {people} сотрудников с этой должностью. Проверьте состав и переведите в «Действует»."
				),
				"rules": [{"position_title": position, "include_subdepartments": 1}],
				"entitlements": [
					{"entitlement": ensure_entitlement(item["access"]), "requirement": MANDATORY}
					for item in items
				],
			}
		).insert(ignore_permissions=True)
		created.append(role.name)
	return created


def refresh_counters():
	"""Entitlement.holders and Access Role.members for list views."""
	model = RoleModel()
	actual, _other = actual_entitlements()
	holders = Counter()
	for held in actual.values():
		holders.update(held.keys())
	for name, current in frappe.get_all(
		"Entitlement", fields=["name", "holders"], as_list=True, limit_page_length=0
	):
		if (current or 0) != holders.get(name, 0):
			frappe.db.set_value("Entitlement", name, "holders", holders.get(name, 0), update_modified=False)
	members = Counter()
	for person in model.persons:
		members.update(model.roles_of(person).keys())
	for name, current in frappe.get_all(
		"Access Role", fields=["name", "members"], as_list=True, limit_page_length=0
	):
		if (current or 0) != members.get(name, 0):
			frappe.db.set_value("Access Role", name, "members", members.get(name, 0), update_modified=False)
