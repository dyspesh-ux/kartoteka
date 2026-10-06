app_name = "access_registry"
app_title = "Access Registry"
app_publisher = "Access Registry contributors"
app_description = "Реестр «кто есть кто и у кого какой доступ»: кадры 1С:ЗУП, права 1С, Active Directory, Битрикс24, роли доступа и бизнес-процессы"
app_email = "noreply@example.com"
app_license = "mit"
required_apps = ["frappe"]

before_install = "access_registry.install.create_roles"
after_install = "access_registry.install.after_install"
after_migrate = "access_registry.install.after_migrate"

# Sync Log retention in days; adjustable in Log Settings.
default_log_clearing_doctypes = {"Sync Log": [180]}

# own name and logo instead of the platform's (see branding.py)
app_logo_url = "/assets/access_registry/images/registry-logo.svg"
website_context = {
	"favicon": "/assets/access_registry/images/registry-logo.svg",
	"splash_image": "/assets/access_registry/images/registry-logo.svg",
}
boot_session = "access_registry.branding.boot_session"

# desk in full width by default (wide report tables); users can still switch it off
app_include_js = "/assets/access_registry/js/registry_desk.js"

# Day: every 30 minutes from 07:00 to 21:00; night: once an hour.
# The jobs only enqueue one "long" job per enabled source. Two methods, because Frappe keys
# Scheduled Job Type by method: one method with two cron lines would keep only one of them.
# Frappe names a Scheduled Job Type by the last two parts of the method («sync.scheduled_ad_sync»):
# they must differ between jobs, or one job silently replaces another (test_scheduler_names).
# Names and titles from the sources longer than a Data field (140) are shortened, not an error.
doc_events = {"*": {"before_validate": "access_registry.fit.fit_lengths"}}

scheduler_events = {
	"cron": {
		"*/30 7-20 * * *": ["access_registry.sync.engine.scheduled_sync_day"],
		"0 21-23,0-6 * * *": ["access_registry.sync.engine.scheduled_sync_night"],
		# Rights of 1C users: nightly snapshot and the event log every 15 minutes.
		"0 3 * * *": ["access_registry.access_catalog.pull.scheduled_snapshot"],
		"*/15 * * * *": ["access_registry.access_catalog.pull.scheduled_log"],
		# Active Directory: every hour at :20 (away from the HR runs at :00 and :30).
		"20 * * * *": ["access_registry.active_directory.sync.scheduled_ad_sync"],
		# Bitrix24: every hour at :40, after AD (users are linked to employees through AD accounts).
		"40 * * * *": ["access_registry.bitrix24.sync.scheduled_b24_sync"],
		# Snipe-IT (equipment): every hour at :25, after AD (users are linked to employees through AD).
		"25 * * * *": ["access_registry.it_assets.sync.scheduled_snipeit_sync"],
		# Notification rules (who gets what): due rules every 15 minutes.
		"3,18,33,48 * * * *": ["access_registry.notify.scheduled"],
		# Bitrix24 smart processes (helpdesk requests): every 15 minutes, only the state of items.
		"7,22,37,52 * * * *": ["access_registry.bitrix24.smart_items.scheduled_smart_sync"],
		# Role model: holders of entitlements and members of roles for list views.
		"30 4 * * *": ["access_registry.access_roles.api.scheduled_refresh"],
		# Suppressed alerts whose date has passed show again.
		"5 0 * * *": ["access_registry.access_roles.suppression.expire"],
		# Morning digest: checked hourly at :05, sent once a day in the hour chosen in its settings.
		"5 * * * *": ["access_registry.access_roles.digest.scheduled"],
		# Figures of the day for the management dashboard (the last count of the day stays).
		"50 * * * *": ["access_registry.registry.metrics.scheduled"],
	}
}
