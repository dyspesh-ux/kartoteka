"""Tests of the registry's own name in the admin and of the SSO-only sign-in page."""

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from access_registry import branding
from access_registry.www import login as login_page


class TestBranding(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.db.rollback()

	def test_names_and_menus(self):
		frappe.db.set_single_value("System Settings", "app_name", "Frappe")
		frappe.db.set_single_value("Website Settings", "app_name", "Своё имя")
		branding.apply()
		self.assertEqual(frappe.db.get_single_value("System Settings", "app_name"), branding.APP_NAME)
		# what the administrator set stays
		self.assertEqual(frappe.db.get_single_value("Website Settings", "app_name"), "Своё имя")
		self.assertEqual(frappe.db.get_single_value("Website Settings", "disable_signup"), 1)

		branding.apply()  # twice: one menu item
		navbar = frappe.get_single("Navbar Settings")
		items = [i for i in navbar.settings_dropdown if i.route == branding.APP_ROUTE]
		self.assertEqual(len(items), 1)
		self.assertEqual(navbar.settings_dropdown[0].route, branding.APP_ROUTE)
		for row in navbar.help_dropdown:
			if row.item_label in branding.HIDDEN_HELP_ITEMS:
				self.assertTrue(row.hidden, row.item_label)

	def test_boot_tells_the_desk_about_the_app(self):
		boot = frappe._dict()
		branding.boot_session(boot)
		self.assertTrue(boot.registry_app["can_open"])
		self.assertEqual(boot.registry_app["route"], "/registry")


class TestSsoLogin(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Guest")
		frappe.db.delete("Social Login Key")
		frappe.db.set_single_value("System Settings", "disable_user_pass_login", 0)
		frappe.form_dict.pop("local", None)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.form_dict.pop("local", None)
		frappe.local.request = None
		frappe.db.rollback()

	def context(self, local=False, redirect="/registry"):
		builder = EnvironBuilder(
			path="/login", query_string={"redirect-to": redirect}, base_url="http://test.local"
		)
		frappe.local.request = Request(builder.get_environ())
		if local:
			frappe.form_dict["local"] = "1"
		return login_page.get_context(frappe._dict())

	def provider(self):
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Social Login Key",
				"social_login_provider": "Custom",
				"provider_name": "Keycloak",
				"enable_social_login": 1,
				"client_id": "registry",
				"client_secret": "secret",
				"base_url": "https://sso.example.local/realms/corp",
				"authorize_url": "/protocol/openid-connect/auth",
				"access_token_url": "/protocol/openid-connect/token",
				"api_endpoint": "/protocol/openid-connect/userinfo",
				"redirect_url": "/api/method/frappe.integrations.oauth2_logins.custom/keycloak",
				"auth_url_data": '{"response_type": "code", "scope": "openid email"}',
				"user_id_property": "sub",
			}
		).insert()
		frappe.set_user("Guest")

	def test_password_form_until_sso_is_set_up(self):
		c = self.context()
		self.assertFalse(c.providers)
		self.assertTrue(c.show_password)
		self.assertEqual(c.app_name, branding.APP_NAME)

	def test_only_sso_when_a_provider_is_enabled(self):
		self.provider()
		c = self.context()
		self.assertEqual([p["provider_name"] for p in c.providers], ["Keycloak"])
		self.assertFalse(c.show_password)
		# the administrator's emergency entry
		self.assertTrue(self.context(local=True).show_password)

	def test_password_switched_off_completely(self):
		self.provider()
		frappe.db.set_single_value("System Settings", "disable_user_pass_login", 1)
		self.assertFalse(self.context(local=True).show_password)

	def test_redirect_is_escaped_in_the_page(self):
		# the address of the page goes into an attribute: a quote must not open the page to a script
		c = self.context(redirect='/x"><img src=x onerror=alert(1)>')
		html = frappe.render_template("access_registry/www/login.html", c)
		self.assertNotIn("<img src=x", html)
		self.assertIn('data-redirect="http://test.local/x&#34;&gt;&lt;img', html)
