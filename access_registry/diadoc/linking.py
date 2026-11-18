"""Links a Diadoc user to an employee (Person).

Order of attempts:
1. Login as an email -> AD Account by user_principal_name -> its employee.
2. The same email -> B24 User by email -> its employee.
3. Normalized full name -> exactly one Person; among namesakes the only one who works wins.
A manual link (Diadoc User.manual_person) always wins and is applied by the document controller.
"""

import frappe

from access_registry.access_catalog.linking import NameMatcher

BY_EMAIL_AD = "Учётка AD (почта)"
BY_EMAIL_B24 = "Битрикс24 (почта)"
BY_NAME = "ФИО"

AD_BY_MAIL = """select person, user_principal_name from `tabAD Account`
	where missing_in_source = 0 and person is not null and person != ''
	and user_principal_name is not null and user_principal_name != ''"""
B24_BY_MAIL = """select person, email from `tabB24 User`
	where active = 1 and missing_in_source = 0 and person is not null and person != ''
	and email is not null and email != ''"""


class DiadocResolver:
	def __init__(self):
		self.by_mail = {}
		for person, upn in frappe.db.sql(AD_BY_MAIL):
			self.by_mail[upn.strip().lower()] = (person, BY_EMAIL_AD)
		for person, email in frappe.db.sql(B24_BY_MAIL):
			self.by_mail.setdefault(email.strip().lower(), (person, BY_EMAIL_B24))
		self.names = NameMatcher()

	def resolve(self, full_name: str, login: str) -> tuple[str | None, str, str]:
		"""Returns (person, method, note); note explains why nobody was found."""
		mail = (login or "").strip().lower()
		if "@" in mail:
			found = self.by_mail.get(mail)
			if found:
				return found[0], found[1], ""
		person, note = self.names.match(full_name or "")
		if person:
			return person, BY_NAME, ""
		return None, "", note
