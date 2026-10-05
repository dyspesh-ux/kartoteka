"""Sign-in page of the registry: corporate single sign-on only.

The page shows the SSO providers enabled in «Social Login Key» (Microsoft Entra ID / Microsoft 365,
Keycloak, ADFS or any OpenID Connect provider). Sign-in by password is not offered:
- while no provider is set up yet (first installation), the page shows the password form so the
  administrator can sign in and set up SSO;
- for an emergency the administrator opens /login?local=1, unless password sign-in is switched off
  completely (System Settings → «Disable Username/Password Login»): then it is refused by the server too.
Redirects of signed-in users and the provider links come from the platform's own login page.
"""

import frappe
from frappe.utils import cint
from frappe.www import login as platform_login

from access_registry.branding import APP_NAME, LOGO

no_cache = 1


def get_context(context):
	platform_login.get_context(context)  # raises a redirect for signed-in users
	providers = context.get("provider_logins") or []
	password_off = cint(frappe.get_system_settings("disable_user_pass_login"))
	asked_local = cint(frappe.form_dict.get("local"))
	context.update(
		{
			"title": APP_NAME,
			"app_name": APP_NAME,
			"logo": LOGO,
			"providers": providers,
			"password_off": password_off,
			"show_password": not password_off and (not providers or asked_local),
			"sso_missing": not providers,
			"redirect_to": platform_login.sanitize_redirect(frappe.local.request.args.get("redirect-to"))
			or "",
		}
	)
	return context
