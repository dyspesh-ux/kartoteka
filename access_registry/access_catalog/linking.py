"""Links a user of any 1C base to an employee (Person) of the HR layer.

Order of attempts:
1. ZUP base: GUID of the physical person from the user card → Person Source ID of the same base.
2. Any base: normalized full name (person presentation, else user name) → exactly one Person;
   among several namesakes the only one who works wins.
A manual link (IB User.manual_person) always wins and is applied by the IB User controller.
Later stages add the link through the AD account (ad_login).
"""

from collections import defaultdict

import frappe

from access_registry.access_registry.doctype.info_base.info_base import is_hr_source
from access_registry.sync.normalize import normalize_name

BY_GUID = "GUID физлица (ЗУП)"
BY_NAME = "ФИО"


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
		self.by_name = defaultdict(list)
		for row in frappe.get_all("Person", fields=["name", "name_key", "status"], limit_page_length=0):
			if row.name_key:
				self.by_name[row.name_key].append(row)

	def resolve(self, user: dict, ib: dict | None) -> tuple[str | None, str, str]:
		"""Returns (person, method, note); note explains why nobody was found."""
		person_id = user.get("person_id")
		guid_note = ""
		if self.use_guid and person_id:
			person = self.by_guid.get(person_id)
			if person:
				return person, BY_GUID, ""
			guid_note = "физлицо из карточки не найдено в кадровых данных базы; "

		full_name = user.get("person_name") or user.get("name") or (ib or {}).get("full_name") or ""
		key = normalize_name(full_name)
		if not key:
			return None, "", guid_note + "ФИО не указано"
		candidates = self.by_name.get(key, [])
		if len(candidates) == 1:
			return candidates[0].name, BY_NAME, ""
		if len(candidates) > 1:
			working = [c for c in candidates if c.status == "Работает"]
			if len(working) == 1:
				return working[0].name, BY_NAME, ""
			return None, "", guid_note + f"несколько сотрудников с ФИО «{full_name}» ({len(candidates)})"
		return None, "", guid_note + f"сотрудник с ФИО «{full_name}» не найден"
