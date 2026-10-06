"""Long names from the sources are shortened to the field instead of failing the load."""

import frappe
from frappe.tests.utils import FrappeTestCase

from access_registry.fit import fit

MONITOR = (
	"#00321 - Монитор Xiaomi 2K A27Qi 2026 [120 Hz, 2560x1440, IPS, 6ms, 300 cd/m², 100% sRGB, "
	"TÜV Low Blue Light, 1*HDMI, 1*DP, AUDIO OUT, DC jack, Tilt]"
)


class TestFit(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_fit(self):
		self.assertEqual(fit("коротко"), "коротко")
		self.assertEqual(len(fit(MONITOR)), 140)
		self.assertTrue(fit(MONITOR).endswith("…"))

	def test_long_name_from_source_is_saved(self):
		self.assertGreater(len(MONITOR), 140)
		doc = frappe.get_doc(
			{"doctype": "IT Asset Event", "uid": "TEST:fit-1", "item_name": MONITOR, "target_name": MONITOR}
		).insert(ignore_permissions=True)
		self.assertEqual(len(doc.item_name), 140)
		self.assertTrue(doc.item_name.startswith("#00321 - Монитор Xiaomi"))

	def test_typed_fields_are_not_cut(self):
		# fields people fill in keep the usual error: nothing is lost silently
		doc = frappe.get_doc({"doctype": "Registry Access Profile", "profile_name": "x" * 200})
		self.assertRaises(frappe.CharacterLengthExceededError, doc.insert, ignore_permissions=True)
