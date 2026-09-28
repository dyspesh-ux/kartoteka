"""Links a user of any 1C base to an employee (Person) of the HR layer.

Order of attempts:
1. ZUP base: GUID of the physical person from the user card → Person Source ID of the same base.
2. AD account: «Пользователь ОС» of the 1C user (domain and login) → AD Account → its employee.
3. Normalized full name (person presentation, else user name) → exactly one Person;
   among several namesakes the only one who works wins.
A manual link (IB User.manual_person) always wins and is applied by the IB User controller.
"""

from collections import defaultdict

import frappe

from access_registry.access_registry.doctype.info_base.info_base import is_hr_source
from access_registry.sync.normalize import normalize_name

BY_GUID = "GUID физлица (ЗУП)"
BY_AD = "Учётка AD"
BY_NAME = "ФИО"


class NameMatcher:
	"""Finds an employee by normalized full name. Shared by 1C users and AD accounts."""

	def __init__(self):
		self.by_name = defaultdict(list)
		for row in frappe.get_all("Person", fields=["name", "name_key", "status"], limit_page_length=0):
			if row.name_key:
				self.by_name[row.name_key].append(row)

	def match(self, full_name: str) -> tuple[str | None, str]:
		"""Returns (person, note); note explains why nobody was found."""
		key = normalize_name(full_name)
		if not key:
			return None, "ФИО не указано"
		candidates = self.by_name.get(key, [])
		if len(candidates) == 1:
			return candidates[0].name, ""
		if len(candidates) > 1:
			working = [c for c in candidates if c.status == "Работает"]
			if len(working) == 1:
				return working[0].name, ""
			return None, f"несколько сотрудников с ФИО «{full_name}» ({len(candidates)})"
		return None, f"сотрудник с ФИО «{full_name}» не найден"


def ad_account_index() -> dict:
	"""(netbios domain, login) in lower case → (AD Account, its employee)."""
	netbios = dict(frappe.get_all("AD Domain", fields=["name", "netbios_name"], as_list=True))
	index = {}
	for row in frappe.get_all(
		"AD Account",
		filters={"missing_in_source": 0},
		fields=["name", "domain", "sam_account_name", "person"],
		limit_page_length=0,
	):
		domain = (netbios.get(row.domain) or "").lower()
		if domain and row.sam_account_name:
			index[(domain, row.sam_account_name.lower())] = (row.name, row.person)
	return index


class PersonResolver:
	def __init__(self, base: str, configuration: str | None):
		self.use_guid = is_hr_source(configuration)
		self.by_guid = {}
		if self.use_guid:
			self.by_guid = dict(
				frappe.get_all(
					"Person Source ID",
					filters={"source": base, "parenttype": "Person"},
					fields=["person_guid", "parent"],
					as_list=True,
				)
			)
		self.names = NameMatcher()
		self.ad = ad_account_index()

	def ad_account(self, ib: dict | None) -> tuple[str | None, str | None]:
		ib = ib or {}
		key = ((ib.get("ad_domain") or "").lower(), (ib.get("ad_login") or "").lower())
		return self.ad.get(key, (None, None)) if all(key) else (None, None)

	def resolve(self, user: dict, ib: dict | None) -> tuple[str | None, str, str]:
		"""Returns (person, method, note); note explains why nobody was found."""
		person_id = user.get("person_id")
		note = ""
		if self.use_guid and person_id:
			person = self.by_guid.get(person_id)
			if person:
				return person, BY_GUID, ""
			note = "физлицо из карточки не найдено в кадровых данных базы; "

		_account, ad_person = self.ad_account(ib)
		if ad_person:
			return ad_person, BY_AD, ""

		full_name = user.get("person_name") or user.get("name") or (ib or {}).get("full_name") or ""
		person, name_note = self.names.match(full_name)
		if person:
			return person, BY_NAME, ""
		return None, "", note + name_note
