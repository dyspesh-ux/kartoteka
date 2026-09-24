app_name = "access_registry"
app_title = "Access Registry"
app_publisher = "Access Registry contributors"
app_description = "Реестр «кто есть кто и у кого какой доступ»: зеркало кадровых данных 1С:ЗУП"
app_email = "noreply@example.com"
app_license = "mit"
required_apps = ["frappe"]

after_install = "access_registry.install.after_install"
after_migrate = "access_registry.install.after_migrate"

# Day: every 30 minutes from 07:00 to 21:00; night: once an hour.
# The jobs only enqueue one "long" job per enabled source.
scheduler_events = {
	"cron": {
		"*/30 7-20 * * *": ["access_registry.sync.engine.enqueue_all_sources"],
		"0 21-23,0-6 * * *": ["access_registry.sync.engine.enqueue_all_sources"],
	}
}
