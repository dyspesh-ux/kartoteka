# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet

# the regulation opens as a link in the app and the desk: only a web address or a path of this site
# (the platform takes «javascript:…» for a valid URL too)
SAFE_LINK = re.compile(r"^(https?://|/(?![/\\]))", re.IGNORECASE)


class BusinessProcess(NestedSet):
	nsm_parent_field = "parent_business_process"

	def validate(self):
		if self.level == "Группа процессов":
			self.is_group = 1
		self.regulation_url = (self.regulation_url or "").strip() or None
		if self.regulation_url and not SAFE_LINK.match(self.regulation_url):
			frappe.throw(
				_("Ссылка на регламент должна начинаться с http://, https:// или / (путь на этом сайте)")
			)
