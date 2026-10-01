// Access Registry: the desk opens in full width by default — report tables of the registry are wide.
// Frappe keeps the choice in localStorage («Toggle Full Width» in the user menu); a user who switched
// it off keeps it off.
(function () {
	try {
		if (window.localStorage.getItem("container_fullwidth") === null) {
			window.localStorage.setItem("container_fullwidth", "true");
		}
	} catch (e) {
		/* storage may be unavailable: Frappe falls back to its default */
	}
})();
