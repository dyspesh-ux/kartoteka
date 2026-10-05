// Access Registry in the admin (desk).
// 1. Full width by default — report tables of the registry are wide. The choice is kept in
//    localStorage («Toggle Full Width» in the user menu); a user who switched it off keeps it off.
// 2. A button «Приложение реестра» in the top bar, next to the help menu, for those who may open
//    the app (frappe.boot.registry_app, see branding.boot_session).
(function () {
	try {
		if (window.localStorage.getItem("container_fullwidth") === null) {
			window.localStorage.setItem("container_fullwidth", "true");
		}
	} catch (e) {
		/* storage may be unavailable: the default width is used */
	}

	function addAppLink() {
		const app = window.frappe && frappe.boot && frappe.boot.registry_app;
		if (!app || !app.can_open || document.querySelector(".registry-app-link")) return;
		// the right-hand menu of the top bar (the one with the user menu), not the breadcrumbs
		const user = document.querySelector("header.navbar .dropdown-navbar-user");
		const nav = user && user.parentElement;
		if (!nav) return;
		const li = document.createElement("li");
		li.className = "nav-item registry-app-link";
		const a = document.createElement("a");
		a.className = "btn btn-primary btn-sm";
		a.href = app.route;
		a.style.cssText = "margin-right:12px;white-space:nowrap;border-radius:999px;background:#2e9e6a;border-color:#2e9e6a;color:#fff";
		a.textContent = __("Приложение реестра") + " →";
		a.title = __("Открыть приложение для пользователей: обзор, контроль, отчёты");
		li.appendChild(a);
		nav.insertBefore(li, nav.firstChild);
	}

	if (window.jQuery) {
		jQuery(document).on("toolbar_setup", addAppLink);
		jQuery(addAppLink);
	}
})();
