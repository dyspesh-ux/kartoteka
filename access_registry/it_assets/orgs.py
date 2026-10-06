"""Equipment bought for one organization and handed out to an employee of another.

The company of an asset in Snipe-IT (whom it was bought for) is matched to an organization of the HR
data by its name: legal forms, quotes and punctuation are ignored («ООО &quot;Альфа&quot;» = «Альфа,
ООО» = «Общество с ограниченной ответственностью «Альфа»»). Names that do not match are mapped by
hand on «Snipe-IT Server» (table «Соответствие компаний»).

Organizations are compared as legal entities: the same company loaded from two ZUP bases is one
organization. An employee belongs to every organization where they work now (main place and
part-time jobs); a dismissed employee to the organization of the last job.
"""

import html
import re
from collections import defaultdict

import frappe

LEGAL_FORMS = (
	"общество с ограниченной ответственностью",
	"публичное акционерное общество",
	"непубличное акционерное общество",
	"закрытое акционерное общество",
	"открытое акционерное общество",
	"акционерное общество",
	"индивидуальный предприниматель",
	"автономная некоммерческая организация",
	"некоммерческая организация",
	"ооо",
	"пао",
	"зао",
	"оао",
	"ао",
	"ип",
	"ано",
	"нко",
	"llc",
	"ltd",
	"inc",
	"gmbh",
)
WORKING = ("Работает", "Увольняется")


def normalize_org(name) -> str:
	text = html.unescape(str(name or "")).lower().replace("ё", "е")
	text = re.sub(r"[«»\"'“”„`]", " ", text)
	text = re.sub(r"[^\w\s-]", " ", text)
	for form in LEGAL_FORMS:
		text = re.sub(rf"(^|\s){re.escape(form)}(\s|$)", " ", text)
	return re.sub(r"\s+", " ", text).strip()


class OrgMatcher:
	"""Snipe-IT companies and employees → organizations (as legal entities)."""

	def __init__(self):
		self.key_of_org, self.title_of_key, by_name = {}, {}, defaultdict(set)
		entities, entity_full = {}, {}
		for e in frappe.get_all("Legal Entity", fields=["name", "title", "full_title"]):
			entities[e.name], entity_full[e.name] = e.title, e.full_title
		for o in frappe.get_all(
			"HR Organization", fields=["name", "title", "full_title", "legal_entity", "missing"]
		):
			key = o.legal_entity or o.name
			self.key_of_org[o.name] = key
			if key not in self.title_of_key or not o.missing:
				self.title_of_key[key] = entities.get(o.legal_entity) or o.title or o.name
			for title in (
				o.title,
				o.full_title,
				entities.get(o.legal_entity),
				entity_full.get(o.legal_entity),
			):
				if normalize_org(title):
					by_name[normalize_org(title)].add(key)
		self.by_name = by_name
		self.manual = {}
		for row in frappe.get_all(
			"Snipe-IT Company Link",
			filters={"parenttype": "Snipe-IT Server"},
			fields=["company", "organization"],
		):
			if row.organization in self.key_of_org:
				self.manual[normalize_org(row.company)] = self.key_of_org[row.organization]

	def company(self, company) -> str | None:
		"""Organization key of a Snipe-IT company; None — no match or more than one."""
		norm = normalize_org(company)
		if not norm:
			return None
		if norm in self.manual:
			return self.manual[norm]
		keys = self.by_name.get(norm) or set()
		return next(iter(keys)) if len(keys) == 1 else None

	def title(self, key) -> str:
		return self.title_of_key.get(key, key or "")

	def people(self, persons) -> dict:
		"""{person: set of organization keys}: where they work now, else the last job."""
		result = defaultdict(set)
		latest = {}
		if not persons:
			return result
		for e in frappe.get_all(
			"Employment",
			filters={"person": ["in", list(persons)]},
			fields=["person", "organization", "status", "hire_date"],
			order_by="hire_date asc",
		):
			key = self.key_of_org.get(e.organization)
			if not key:
				continue
			if e.status in WORKING:
				result[e.person].add(key)
			latest[e.person] = key
		for person, key in latest.items():
			if person not in result:
				result[person].add(key)
		return result


def other_org_assets(assets, matcher: OrgMatcher | None = None):
	"""Assets handed out to employees of another organization, and companies that matched nothing.

	``assets`` — dicts with ``company`` and ``person``. Returns (rows, unmatched), where a row is the
	asset plus ``company_org``, ``person_orgs`` (titles) and ``person_org_keys``; ``unmatched`` is
	{company: number of assets}."""
	matcher = matcher or OrgMatcher()
	held = [a for a in assets if a.get("person") and a.get("company")]
	orgs = matcher.people({a["person"] for a in held})
	rows, unmatched = [], defaultdict(int)
	for a in held:
		key = matcher.company(a["company"])
		if key is None:
			unmatched[html.unescape(a["company"])] += 1
			continue
		person_keys = orgs.get(a["person"]) or set()
		if person_keys and key not in person_keys:
			rows.append(
				frappe._dict(
					a,
					company_org=matcher.title(key),
					person_orgs=", ".join(sorted(matcher.title(k) for k in person_keys)),
				)
			)
	return rows, dict(unmatched)
