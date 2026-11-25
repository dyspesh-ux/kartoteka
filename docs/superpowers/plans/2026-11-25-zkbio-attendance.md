# ZKBio CVAccess (СКУД) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подключить СКУД ZKBio CVAccess (PostgreSQL) к реестру: зеркалить персонал и события проходов, привязывать к сотрудникам по ФИО, строить отчёты «приход/уход» с исключением отпусков/командировок.

**Architecture:** Pull-модель по образцу `active_directory`: Frappe по расписанию читает PostgreSQL read-only (psycopg2), синк в одном savepoint с предохранителем, Sync Log вида «СКУД». Три DocType (`ZKBio Server/Person/Event`), два Script Report, workspace «СКУД».

**Tech Stack:** Frappe v15, MariaDB, psycopg2-binary (новая зависимость), чистый JS для кнопок формы.

**Spec:** `docs/superpowers/specs/2026-11-25-zkbio-attendance-design.md`

## Global Constraints

- Python 3.10+, Ruff: длина строки 110, **табы**, двойные кавычки (`ruff check access_registry` + `ruff format --check` перед коммитом).
- Код и комментарии — на английском; тексты интерфейса и сообщения пользователю — на русском.
- Источник только читается: подключение PG с `default_transaction_read_only=on`; в ZKBio не пишем никогда.
- Изменяющие действия — только POST/whitelist с проверкой роли (`frappe.only_for(("System Manager", "Registry Admin"))`).
- ФИО и прочие персональные данные не попадают в Sync Log (сообщения — по `pin`/id, не по именам).
- Записи именуются `<код сервера>:<id>`, хранят хеш, пропавшие помечаются, не удаляются.
- Scheduled-методы должны различаться в последних двух частях (`zkbio.sync.scheduled_events_sync` / `scheduled_persons_sync`) — покрыто `test_scheduler_names`.
- Каждый новый DocType — ссылка в workspace (покрыто `test_workspace`).
- Тесты запускаются в bench: `bench --site <site> run-tests --app access_registry` (локально без bench не запускаются; CI — `.github/workflows/ci.yml`).

## Review Focus

1. Пустое/битое `pass_event_nos` («abc», пробелы) не должно молча отключить импорт: пусто → `{0}`, нечисловые значения игнорируются с предупреждением в Sync Log → тест `test_pass_nos_parsing` (Task 1).
2. Первый запуск без watermark не должен тянуть всю 15-месячную историю: старт от `today - 180 дней` → тест `test_first_run_starts_180_days_back` (Task 3).
3. Отсутствующий psycopg2 на сайте — понятная ошибка, а не ImportError: `ValidationError` с инструкцией → тест `test_missing_driver_message` (Task 1).
4. Дубли `pin` в `pers_person` (два человека с одним табельным): обе записи создаются, в Sync Log предупреждение → тест `test_duplicate_pin_warns` (Task 2).
5. Смена ФИО в СКУД (замужество) меняет хеш и перепривязывает по новой ФИО, `manual_person` всё равно главнее → тест `test_rename_relinks_unless_manual` (Task 2).

---

### Task 1: Модуль, `ZKBio Server`, PostgreSQL-клиент

**Files:**
- Create: `access_registry/zkbio/__init__.py` (пустой)
- Create: `access_registry/zkbio/pg_client.py`
- Create: `access_registry/zkbio/doctype/__init__.py`, `.../zkbio_server/{__init__.py,zkbio_server.json,zkbio_server.py,zkbio_server.js}`
- Modify: `access_registry/modules.txt` (добавить строку `ZKBio`)
- Modify: `pyproject.toml` (`dependencies = ["psycopg2-binary"]`)
- Test: `access_registry/tests/test_zkbio.py`

**Interfaces:**
- Consumes: `access_registry.settings.get_settings()` (job/http timeouts), паттерн контроллера `ad_domain.py` (validate + whitelist-кнопки).
- Produces:
  - `pg_client.test_connection(server_doc) -> dict` — `{"persons": int, "events": int, "version": str}`.
  - `pg_client.fetch_persons(server_doc) -> list[dict]` — ключи: `id, pin, last_name, first_name, email, dept_name, hire_date, status, update_time`.
  - `pg_client.fetch_events(server_doc, since: datetime, limit: int) -> list[dict]` — по `event_time > since` order by `event_time`; ключи: `id, event_time, pin, card_no, dev_alias, event_point_name, reader_name, event_no, event_name, verify_mode_name, acc_zone, area_name`.
  - `zkbio.sync.pass_nos(server_doc) -> set[int]` — разбор `pass_event_nos`; в Task 1
    `access_registry/zkbio/sync.py` создаётся только с этой функцией, остальное дополнят Task 2–3.
  - DocType `ZKBio Server` (name = `server_code`).

- [ ] **Step 1: failing tests**

`access_registry/tests/test_zkbio.py`:

```python
"""Tests of the ZKBio (СКУД) module: scaffold, client config, persons/events sync, reports."""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestZKBioScaffold(FrappeTestCase):
	def test_module_scaffold(self):
		for doctype in ("ZKBio Server", "ZKBio Person", "ZKBio Event"):
			self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
		kind = frappe.get_meta("Sync Log").get_field("kind")
		self.assertIn("СКУД", kind.options.split("\n"))
		ws = frappe.get_doc("Workspace", "СКУД")
		linked = {row.link_to for row in ws.links}
		self.assertTrue({"ZKBio Server", "ZKBio Person", "ZKBio Event"} <= linked)


class TestPassNos(FrappeTestCase):
	def test_pass_nos_parsing(self):
		from access_registry.zkbio.sync import pass_nos

		server = frappe._dict(pass_event_nos=None)
		self.assertEqual(pass_nos(server), {0})
		server.pass_event_nos = " 0, 202 ,208 "
		self.assertEqual(pass_nos(server), {0, 202, 208})
		server.pass_event_nos = "0,abc, 14x"
		self.assertEqual(pass_nos(server), {0})  # «abc»/«14x» проигнорированы

	def test_missing_driver_message(self):
		import sys
		from unittest import mock

		from access_registry.zkbio import pg_client

		with mock.patch.dict(sys.modules, {"psycopg2": None}):
			with self.assertRaises(frappe.ValidationError) as ctx:
				pg_client._psycopg2()
		self.assertIn("psycopg2-binary", str(ctx.exception))
```

(тесты sync/отчётов добавятся в Tasks 2, 3, 5, 6 в этот же файл)

- [ ] **Step 2: run — FAIL**

`bench --site <site> run-tests --app access_registry --module access_registry.tests.test_zkbio` → ModuleNotFoundError / DocType не существует.

- [ ] **Step 3: реализация**

`modules.txt`: добавить `ZKBio`. `pyproject.toml`: `dependencies = ["psycopg2-binary"]` (комментарий: драйвер PostgreSQL для СКУД).

`zkbio_server.json` (по образцу `ad_domain.json`): `autoname: "field:server_code"`, `naming_rule: "By fieldname"`, `module: "ZKBio"`, `track_changes: 1`, права System Manager + Registry Admin (полные), Registry Auditor (read/report/export/print). Поля:
`server_code` (Data, unique, reqd, «Код сервера»), `enabled` (Check, default 1), `host` (Data, reqd), `port` (Int, default 5442), `database` (Data, default `biosecurity-boot`, reqd), `username` (Data, reqd), `password` (Password, reqd), `pass_event_nos` (Data, default «0», description «Коды событий-проходов через запятую»), `events_overlap_minutes` (Int, default 10), section «Служебные» (read_only): `events_watermark` (Datetime), `last_sync` (Datetime), `last_status` (Data).

`zkbio_server.py` — контроллер `ZKBioServer(Document)`, по образцу `ad_domain.py`:
- `validate()`: `server_code` по `CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")` (текст ошибки «Код сервера: 1–20 символов, латиница, цифры, «_» и «-».»).
- `@frappe.whitelist() test_connection(self)`: `frappe.only_for(("System Manager", "Registry Admin"))`, вызывает `pg_client.test_connection`, возвращает строку «Подключение успешно: PostgreSQL {version}, персонала {persons}, событий {events}».
- `@frappe.whitelist() def sync_now(self)`: проверка роли, `is_job_enqueued`, enqueue обеих фаз (использует `zkbio.sync.enqueue`, реализуется в Task 2 — в Task 1 вызов внутри `try/except ImportError` не нужен: Task 2 приземлится до прогона; порядок коммитов это допускает, но кнопку в js добавить в Task 2).

`zkbio/pg_client.py`:

```python
"""Read-only PostgreSQL client for ZKBio CVAccess (biosecurity-boot)."""

def _psycopg2():
	"""Imports the driver lazily: the app must install without it; the error must be actionable."""
	try:
		import psycopg2, psycopg2.extras
	except ImportError:
		frappe.throw("Драйвер PostgreSQL (psycopg2-binary) не установлен на сайте — см. README, раздел «СКУД».")
	return psycopg2, psycopg2.extras

def _connect(server):  # readonly session, connect_timeout=10, statement_timeout=60000
def test_connection(server) -> dict
def fetch_persons(server) -> list[dict]   # pers_person p left join auth_department d on d.id = p.auth_dept_id
def fetch_events(server, since, limit) -> list[dict]  # acc_transaction where event_time > %(since)s order by event_time limit %(limit)s
```

SQL персонала: `select p.id, p.pin, p.last_name, p.name as first_name, p.email, p.hire_date, p.status, p.update_time, d.name as dept_name from pers_person p left join auth_department d on d.id = p.auth_dept_id`.
SQL событий: `select id, event_time, pin, card_no, dev_alias, event_point_name, reader_name, event_no, event_name, verify_mode_name, acc_zone, area_name from acc_transaction where event_time > %(since)s order by event_time limit %(limit)s`.
Курсор — `RealDictCursor`; строки — обычные dict.

`zkbio/sync.py` — пока только:

```python
def pass_nos(server) -> set[int]:
	"""Event codes treated as passages; empty config means the default {0}."""
```

- [ ] **Step 4: run — PASS** (scaffold-тест упадёт на Workspace/DocType «ZKBio Person/Event» — они появятся в Tasks 2–3; в этом коммите scaffold-тест проверяет только `ZKBio Server` и опцию «СКУД»: написать его так, а общий вид раскомментировать в Task 3. Решение: scaffold-тест проверяет только то, что уже должно быть — Task 1: `ZKBio Server` + опция kind; Task 2 добавит assert `ZKBio Person`; Task 3 — `ZKBio Event` и workspace.)

- [ ] **Step 5: Commit** `feat(zkbio): module scaffold, ZKBio Server, read-only pg client`

---

### Task 2: `ZKBio Person` и фаза persons

**Files:**
- Create: `access_registry/zkbio/doctype/zkbio_person/{__init__.py,zkbio_person.json,zkbio_person.py}`
- Modify: `access_registry/zkbio/sync.py` (jobs, `run_server_sync`, `ServerSync.sync_persons`)
- Modify: `access_registry/hooks.py` (cron `"45 4 * * *"` → `zkbio.sync.scheduled_persons_sync`)
- Test: `access_registry/tests/test_zkbio.py` (TestZKBioPersonsSync, TestZKBioLinking)

**Interfaces:**
- Consumes: Task 1 (`fetch_persons`, `pass_nos`); `active_directory.sync` паттерн (`_format_messages`, `_switch_user` из `sync.engine`); `access_catalog.linking.NameMatcher` (`match(full_name) -> (person|None, note)`); `access_catalog.importer.save_changed`.
- Produces:
  - `sync.scheduled_persons_sync()`, `sync.enqueue(server, phase)`, `sync.job_id_for(server, phase) -> str` (`f"zkbio_sync::{phase}::{server}"`)
  - `sync.run_server_sync(server: str, phase: str, commit: bool = True, fetch=None) -> Sync Log` — `phase` = `"persons"` | `"events"`.
  - `sync.ServerSync(server_doc, sync_log: str | None)` с `.warn()`, `.stats` (Counter), `.key(zkbio_id) -> str`, `.sync_persons(rows)`.
  - DocType `ZKBio Person` (name = `record_key` = `<сервер>:<id>`).

- [ ] **Step 1: failing tests**

```python
def make_server(code="ТСКУД1"):
	if not frappe.db.exists("ZKBio Server", code):
		return frappe.get_doc({
			"doctype": "ZKBio Server", "server_code": code, "host": "127.0.0.1",
			"username": "ro", "password": "x",
		}).insert(ignore_permissions=True)
	return frappe.get_doc("ZKBio Server", code)


def person_row(zid, last, first, pin="100", dept="Ит", status=0):
	return {"id": zid, "pin": pin, "last_name": last, "first_name": first,
		"email": "", "dept_name": dept, "hire_date": None, "status": status, "update_time": None}


class TestZKBioPersonsSync(FrappeTestCase):
	def test_create_update_missing(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server()
		log = run_server_sync("ТСКУД1", "persons", commit=False,
			fetch=lambda _s: [person_row("p1", "Тестскуд", "Иван"), person_row("p2", "Тестскуд", "Пётр")])
		self.assertEqual(log.status, "Успех")
		self.assertEqual(log.kind, "СКУД")
		self.assertEqual(frappe.db.count("ZKBio Person", {"server": "ТСКУД1"}), 2)
		# повтор без изменений: ни версий, ни обновлений
		versions = frappe.db.count("Version", {"ref_doctype": "ZKBio Person"})
		log = run_server_sync("ТСКУД1", "persons", commit=False,
			fetch=lambda _s: [person_row("p1", "Тестскуд", "Иван"), person_row("p2", "Тестскуд", "Пётр", dept="Бухгалтерия")])
		self.assertEqual(frappe.db.count("Version", {"ref_doctype": "ZKBio Person"}), versions + 1)  # только p2
		# p1 пропал из выгрузки
		run_server_sync("ТСКУД1", "persons", commit=False, fetch=lambda _s: [person_row("p2", "Тестскуд", "Пётр")])
		self.assertEqual(frappe.db.get_value("ZKBio Person", "ТСКУД1:p1", "missing_in_source"), 1)

	def test_guard_trips_on_shrink(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("ТСКУДГ")
		rows = [person_row(f"g{i}", "Тестскудгард", f"Н{i}") for i in range(25)]  # > guard_min_records
		run_server_sync("ТСКУДГ", "persons", commit=False, fetch=lambda _s: rows)
		log = run_server_sync("ТСКУДГ", "persons", commit=False, fetch=lambda _s: rows[:2])
		self.assertEqual(log.status, "Остановлен предохранителем")
		self.assertEqual(frappe.db.count("ZKBio Person", {"server": "ТСКУДГ", "missing_in_source": 0}), 25)

	def test_no_names_in_log(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("ТСКУДЛ")
		log = run_server_sync("ТСКУДЛ", "persons", commit=False,
			fetch=lambda _s: [person_row("x1", "Тестскудтайный", "Неизвестный")])
		self.assertNotIn("Тестскудтайный", log.messages or "")

	def test_duplicate_pin_warns(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("ТСКУДДП")
		log = run_server_sync("ТСКУДДП", "persons", commit=False, fetch=lambda _s: [
			person_row("dp1", "Тестскудпин1", "А", pin="555"),
			person_row("dp2", "Тестскудпин2", "Б", pin="555"),
		])
		self.assertIn("555", log.messages)


class TestZKBioLinking(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.person = frappe.get_doc({"doctype": "Person", "last_name": "Тестскудлинк",
			"first_name": "Лев", "middle_name": "Львович", "status": "Работает"}).insert(ignore_permissions=True)

	def test_link_by_fio(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("ТСКУДП")
		run_server_sync("ТСКУДП", "persons", commit=False,
			fetch=lambda _s: [person_row("lp1", "Тестскудлинк", "Лев")])
		row = frappe.db.get_value("ZKBio Person", "ТСКУДП:lp1", ["person", "person_link_method"], as_dict=True)
		self.assertEqual((row.person, row.person_link_method), (self.person.name, "ФИО"))

	def test_namesake_stays_unlinked(self):
		frappe.get_doc({"doctype": "Person", "last_name": "Тестскуддубль",
			"first_name": "Иван", "status": "Работает"}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Person", "last_name": "Тестскуддубль",
			"first_name": "Иван", "status": "Работает"}).insert(ignore_permissions=True)
		from access_registry.zkbio.sync import run_server_sync

		make_server("ТСКУДД")
		run_server_sync("ТСКУДД", "persons", commit=False,
			fetch=lambda _s: [person_row("d1", "Тестскуддубль", "Иван")])
		row = frappe.db.get_value("ZKBio Person", "ТСКУДД:d1", ["person", "person_link_note"], as_dict=True)
		self.assertFalse(row.person)
		self.assertIn("несколько", row.person_link_note)

	def test_manual_person_wins_and_survives(self):
		from access_registry.zkbio.sync import run_server_sync

		make_server("ТСКУДМ")
		run_server_sync("ТСКУДМ", "persons", commit=False,
			fetch=lambda _s: [person_row("m1", "Тестскудручной", "Мария")])
		doc = frappe.get_doc("ZKBio Person", "ТСКУДМ:m1")
		doc.manual_person = self.person.name
		doc.save(ignore_permissions=True)
		self.assertEqual((doc.person, doc.person_link_method), (self.person.name, "Вручную"))
		run_server_sync("ТСКУДМ", "persons", commit=False,
			fetch=lambda _s: [person_row("m1", "Тестскудлинк", "Лев")])  # ФИО сменилось — ручная всё равно главнее
		doc.reload()
		self.assertEqual(doc.person, self.person.name)

	def test_rename_relinks_unless_manual(self):
		from access_registry.zkbio.sync import run_server_sync

		other = frappe.get_doc({"doctype": "Person", "last_name": "Тестскудновая",
			"first_name": "Анна", "status": "Работает"}).insert(ignore_permissions=True)
		make_server("ТСКУДР")
		run_server_sync("ТСКУДР", "persons", commit=False,
			fetch=lambda _s: [person_row("r1", "Тестскудлинк", "Лев")])
		run_server_sync("ТСКУДР", "persons", commit=False,
			fetch=lambda _s: [person_row("r1", "Тестскудновая", "Анна")])
		self.assertEqual(frappe.db.get_value("ZKBio Person", "ТСКУДР:r1", "person"), other.name)
```

- [ ] **Step 2: run — FAIL** (DocType/функции отсутствуют)

- [ ] **Step 3: реализация**

`zkbio_person.json`: `autoname: "field:record_key"`, `track_changes: 1`, `module: "ZKBio"`, права как у сервера. Поля: `person` (Link Person, read_only, in_list_view, search_index), `last_name`, `first_name` (Data, read_only), `pin` (Data, read_only, in_list_view), `dept_name` (Data, read_only, in_list_view), `email`, `hire_date` (Date), `zk_status` (Int, «Статус в СКУД»), `manual_person` (Link Person), `person_link_method`, `person_link_note` (Data, read_only), section «Источник»: `record_key` (Data, unique, read_only), `server` (Link ZKBio Server, reqd, read_only, search_index), `zkbio_id` (Data, read_only), `src_hash` (Data, read_only), `missing_in_source` (Check, read_only).

`zkbio_person.py`: `validate()`: если `manual_person` — `person = manual_person`, `person_link_method = "Вручную"`, note пустая (по образцу Diadoc User).

`zkbio/sync.py` (дополнить): константы `SAVEPOINT = "zkbio_sync"`, `BY_NAME = "ФИО"`, `BY_MANUAL = "Вручную"`; `scheduled_persons_sync()` (перебор enabled-серверов → `enqueue(server, "persons")`); `run_server_sync` по образцу `active_directory.sync.run_domain_sync`: Sync Log kind «СКУД» (source пуст), savepoint, `_switch_user(settings.sync_user, messages)`, `fetch` или `pg_client.fetch_persons`, `last_sync`/`last_status` на сервере, откат при исключении/предохранителе.

`ServerSync.sync_persons(rows)`:
- предохранитель как `DomainImport.check_guard` (настройки общие), иначе `raise ZKBioGuardTripped(frappe.ValidationError)`;
- дубли `pin` в одной выгрузке — предупреждение в Sync Log (по pin, без ФИО), записи создаются обе;
- upsert по `src_hash` (md5 json полей без `update_time`, как VOLATILE у AD); линковка: `manual_person` у существующей записи не трогаем; иначе `NameMatcher().match(f"{last} {first}")`; при изменении хеша перелинковываем;
- пропавшие → `missing_in_source=1` (только своего сервера);
- сообщения без ФИО (предупреждения по `zkbio_id`/`pin`).

`hooks.py`: добавить в `scheduler_events["cron"]` строку `"45 4 * * *": ["access_registry.zkbio.sync.scheduled_persons_sync"],` (персонал редко меняется — раз в сутки).

Scaffold-тест: раскомментировать assert `ZKBio Person`.

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): ZKBio Person mirror with ФИО linking and guard`

---

### Task 3: `ZKBio Event` и фаза events

**Files:**
- Create: `access_registry/zkbio/doctype/zkbio_event/{__init__.py,zkbio_event.json,zkbio_event.py}` (пустой контроллер)
- Create: `access_registry/access_registry/workspace/скуд/скуд.json`
- Modify: `access_registry/zkbio/sync.py` (`scheduled_events_sync`, `ServerSync.sync_events`, цикл watermark)
- Modify: `access_registry/hooks.py` (cron `"10,25,40,55 * * * *"` → `zkbio.sync.scheduled_events_sync`; `default_log_clearing_doctypes` += `{"ZKBio Event": [180]}`)
- Modify: `access_registry/active_directory/... ` — нет; `access_registry/access_registry/doctype/sync_log/sync_log.json` (kind: добавить опцию `СКУД`)
- Test: `access_registry/tests/test_zkbio.py` (TestZKBioEventsSync)

**Interfaces:**
- Consumes: Task 2 (`run_server_sync`, `ServerSync`), Task 1 (`fetch_events`, `pass_nos`).
- Produces:
  - `sync.scheduled_events_sync()`
  - `ServerSync.sync_events(rows: list[dict]) -> None` — фильтрация и вставка событий; возвращает ничего, статистика в `.stats`.
  - В `run_server_sync` для phase="events": цикл `while True: rows = fetch(server, since, 5000); if not rows: break; sync.sync_events(rows); watermark = max(event_time); if len(rows) < 5000: break` (значение `since` первой итерации = watermark − overlap, либо `today−180д` при пустом).
  - DocType `ZKBio Event` (name = `record_key`).

- [ ] **Step 1: failing tests**

```python
def event_row(eid, dt, pin="100", event_no=0, point="Вход"):
	return {"id": eid, "event_time": dt, "pin": pin, "card_no": "123",
		"dev_alias": "Турникет", "event_point_name": point, "reader_name": "R1",
		"event_no": event_no, "event_name": f"acc_eventNo_{event_no}",
		"verify_mode_name": "Card", "acc_zone": "", "area_name": ""}


def fetch_with(rows):
	"""fetch(server, since, limit): события строго позже since, по возрастанию."""
	def _fetch(_server, since, limit, _rows=sorted(rows, key=lambda r: r["event_time"])):
		return [r for r in _rows if r["event_time"] > since][:limit]
	return _fetch


class TestZKBioEventsSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.person = frappe.get_doc({"doctype": "Person", "last_name": "Тестскудсобытие",
			"first_name": "Елена", "status": "Работает"}).insert(ignore_permissions=True)
		make_server("ТСКУДЕ")
		from access_registry.zkbio.sync import run_server_sync
		run_server_sync("ТСКУДЕ", "persons", commit=False,
			fetch=lambda _s: [person_row("e1", "Тестскудсобытие", "Елена", pin="100")])

	def test_first_run_starts_180_days_back(self):
		from access_registry.zkbio.sync import run_server_sync

		seen_since = []
		log = run_server_sync("ТСКУДЕ", "events", commit=False,
			fetch=lambda s, since, limit: seen_since.append(since) or [])
		self.assertEqual(log.status, "Успех")
		# перекрытие (overlap) применяется только к существующему watermark
		self.assertEqual(seen_since[0].date(), frappe.utils.add_days(frappe.utils.getdate(), -180))

	def test_watermark_dedupe_and_filters(self):
		from datetime import datetime

		from access_registry.zkbio.sync import run_server_sync

		now = datetime.now().replace(microsecond=0)
		rows = [
			event_row("t1", now - timedelta(hours=2), pin="100"),           # проход
			event_row("t2", now - timedelta(hours=1), pin="100", event_no=202),  # не в белом списке
			event_row("t3", now - timedelta(minutes=30), pin=""),            # без pin
			event_row("t4", now, pin="999"),                                 # чужой pin — сирота
		]
		log = run_server_sync("ТСКУДЕ", "events", commit=False, fetch=fetch_with(rows))
		self.assertEqual(log.status, "Успех")
		ev = frappe.db.get_value("ZKBio Event", "ТСКУДЕ:t1",
			["person", "zkbio_person", "event_point_name"], as_dict=True)
		self.assertEqual(ev.person, self.person.name)
		self.assertEqual(ev.zkbio_person, "ТСКУДЕ:e1")
		self.assertFalse(frappe.db.exists("ZKBio Event", "ТСКУДЕ:t2"))  # отфильтрован по коду
		self.assertFalse(frappe.db.exists("ZKBio Event", "ТСКУДЕ:t3"))  # без pin
		self.assertTrue(frappe.db.exists("ZKBio Event", "ТСКУДЕ:t4"))   # сирота хранится без person
		self.assertIsNone(frappe.db.get_value("ZKBio Event", "ТСКУДЕ:t4", "person") or None)
		# watermark на max(event_time); повтор с перекрытием не даёт дублей
		server = frappe.get_doc("ZKBio Server", "ТСКУДЕ")
		self.assertEqual(frappe.utils.get_datetime(server.events_watermark), now)
		log2 = run_server_sync("ТСКУДЕ", "events", commit=False, fetch=fetch_with(rows))
		import json as jsonlib
		stats = jsonlib.loads(log2.stats)
		self.assertEqual(stats["ZKBio Event"].get("created", 0), 0)
		self.assertGreaterEqual(stats["ZKBio Event"].get("duplicates", 0), 1)
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: реализация**

`zkbio_event.json`: `autoname: "field:record_key"`, `track_changes: 0`, `module: "ZKBio"`, `sort_field: "event_time"`, `sort_order: "DESC"`, права как у сервера (чтение аудитору). Поля: `event_time` (Datetime, reqd, in_list_view, search_index), `person` (Link Person, read_only, in_list_view, search_index), `zkbio_person` (Link ZKBio Person, read_only, search_index), `pin` (Data, read_only), `card_no`, `dev_alias`, `event_point_name` (in_list_view), `reader_name`, `event_no` (Int), `event_name`, `verify_mode_name`, `acc_zone`, `area_name` — Data read_only; section «Источник»: `record_key` (unique), `server` (Link ZKBio Server, reqd, search_index), `zkbio_id`.

`sync.py`: `scheduled_events_sync()`; в `run_server_sync` ветка events (watermark-цикл, `frappe.db.set_value("ZKBio Server", server, "events_watermark", watermark)` в конце). `ServerSync.sync_events(rows)`:
- карта `pin → (zkbio_person_name, person)` по `ZKBio Person` сервера (`missing_in_source=0`);
  при дублях pin (предупреждение уже выдано фазой persons) берётся первая запись;
- пропуск: пустой pin (`stats["ZKBio Event"]["skipped_no_pin"]`), `event_no ∉ pass_nos` (`skipped_event_no`); существующий ключ (`duplicates`);
- вставка: `record_key = key(id)`, `person`/`zkbio_person` из карты (пусто у сирот), остальное из строки; `event_time` через `get_datetime`;
- хеши/версии не нужны (append-only).

`sync_log.json`: в `kind.options` добавить строку `СКУД`.
`hooks.py`: cron событий + `default_log_clearing_doctypes = {"Sync Log": [180], "ZKBio Event": [180]}`.
Workspace `скуд/скуд.json` (по образцу diadoc.json, parent_page "Access Registry", module "Access Registry", roles как у «Диадок» без «Diadoc Specialist»): карточка «СКУД» со ссылками DocType `ZKBio Server`, `ZKBio Person`, `ZKBio Event`, `Sync Log`; отчёты добавятся в Task 5–6 (ссылки Link type Report — тогда же; test_workspace требует существования Report — поэтому ссылки на отчёты добавлять в Tasks 5–6, не сейчас).
Scaffold-тест: раскомментировать assert `ZKBio Event` и workspace.

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): ZKBio Event mirror with watermark, filters, dedupe`

---

### Task 4: Кнопки формы сервера (включая «Перечитать за N дней»)

**Files:**
- Create: `access_registry/zkbio/doctype/zkbio_server/zkbio_server.js`
- Modify: `access_registry/zkbio/doctype/zkbio_server/zkbio_server.py` (`sync_now`, `refetch_events`)
- Test: `access_registry/tests/test_zkbio.py` (TestZKBioPermissions)

**Interfaces:**
- Consumes: `sync.enqueue`, `sync.job_id_for` (Task 2), `pg_client.test_connection` (Task 1).
- Produces: `ZKBioServer.refetch_events(days: int)` — whitelist, admin-only; сдвигает
  `events_watermark` на `now - days` (days ограничен 1..180), сообщение-ответ.

- [ ] **Step 1: failing test**

```python
class TestZKBioPermissions(FrappeTestCase):
	def test_buttons_require_admin(self):
		from access_registry.tests.test_permissions import make_user

		make_server("ТСКУДПР")
		nobody = make_user("zkbio-nobody@registry.test")
		doc = frappe.get_doc("ZKBio Server", "ТСКУДПР")
		frappe.set_user(nobody)
		try:
			with self.assertRaises(frappe.PermissionError):
				doc.sync_now()
			with self.assertRaises(frappe.PermissionError):
				doc.test_connection()
			with self.assertRaises(frappe.PermissionError):
				doc.refetch_events(7)
		finally:
			frappe.set_user("Administrator")

	def test_refetch_shifts_watermark(self):
		from frappe.utils import add_days, now_datetime

		make_server("ТСКУДСБ")
		doc = frappe.get_doc("ZKBio Server", "ТСКУДСБ")
		doc.db_set("events_watermark", now_datetime())
		doc.refetch_events(7)
		doc.reload()
		self.assertEqual(frappe.utils.get_datetime(doc.events_watermark).date(),
			add_days(frappe.utils.getdate(), -7))
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: реализация**

`zkbio_server.js` по образцу `ad_domain.js`: кнопки «Проверить подключение» → `frm.call("test_connection")` (msgprint), «Синхронизировать сейчас» → `frm.call("sync_now")`, «Перечитать за N дней» → `frappe.prompt` (Int, default 7) → `frm.call("refetch_events", {days})`; группа «Открыть»: «Персонал СКУД» (List ZKBio Person, filter server), «События СКУД», «Журнал синхронизаций» (List Sync Log, kind=«СКУД»); `set_headline` «Реестр только читает PostgreSQL СКУД. Учётка не должна иметь прав на запись.», зелёный.
`sync_now`: `frappe.only_for(...)`, проверка `is_job_enqueued`, enqueue `persons` затем `events` (персонал первым — события сразу привяжутся), ответ-строка как у AD.
`refetch_events(days)`: `frappe.only_for(...)`, `days = min(max(cint(days), 1), 180)`, `db_set("events_watermark", add_days(now_datetime(), -days))`, вернуть строку «Окно событий сдвинуто на N дней назад; ближайший синк перечитает события (дубликаты пропускаются).»

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): server form buttons and permission checks`

---

### Task 5: Отчёт «СКУД: приходы и уходы»

**Files:**
- Create: `access_registry/zkbio/report/__init__.py`, `.../zkbio_attendance/{__init__.py,zkbio_attendance.json,zkbio_attendance.py}`
- Modify: workspace `скуд/скуд.json` (ссылка Report «ZKBio Attendance»)
- Test: `access_registry/tests/test_zkbio.py` (TestZKBioAttendanceReport)

**Interfaces:**
- Consumes: `ZKBio Event` (Task 3), `HR Absence` (поля: person, date_from, date_to, until_expected, cancelled), `Person.status`, `Employment.department` (активные: `status="Работает"`, `missing=0`).
- Produces: `execute(filters) -> (columns, rows)`; filters: `date_from`, `date_to` (Date, reqd), `department` (Link HR Department), `show_absent` (Check, default 0). Колонки: `day` (Date), `person_name`, `person` (Link), `department`, `first_event` (Time), `last_event` (Time), `events_count` (Int), `points`, `absence`.

- [ ] **Step 1: failing test**

Фикстура: трое сотрудников СКУД, у всех есть события (привязка фиксируется при синке
событий); **после** синка событий одному ставим отпуск на сегодня (`HR Absence`), другому —
статус «Уволен». Так отчёт проверяется на готовых событиях, а исключения — кадровыми данными.

```python
class TestZKBioAttendanceReport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from access_registry.zkbio.sync import run_server_sync

		cls.day = frappe.utils.getdate()
		cls.present = make_hr_person("Тестскудотчёт", "Пётр")
		cls.vacationer = make_hr_person("Тестскудотчёт", "Вера")
		cls.dismissed = make_hr_person("Тестскудотчёт", "Уволен")
		make_server("ТСКУДО")
		run_server_sync("ТСКУДО", "persons", commit=False, fetch=lambda _s: [
			person_row("o1", "Тестскудотчёт", "Пётр", pin="501"),
			person_row("o2", "Тестскудотчёт", "Вера", pin="502"),
			person_row("o3", "Тестскудотчёт", "Уволен", pin="503"),
		])
		dt = lambda h: frappe.utils.get_datetime(f"{cls.day} {h:02d}:00:00")
		events = [
			event_row("a1", dt(9), pin="501"), event_row("a2", dt(18), pin="501"),
			event_row("a3", dt(10), pin="502"), event_row("a4", dt(11), pin="503"),
		]
		run_server_sync("ТСКУДО", "events", commit=False, fetch=fetch_with(events))
		# после синка: Вера в отпуске на сегодня, Уволен — уволен
		frappe.get_doc({"doctype": "HR Absence", "source": "ZUP1", "employment_guid": "g-test",
			"state_code": "ОТ", "state": "Отпуск ежегодный", "category": "Отпуск",
			"date_from": cls.day, "date_to": cls.day, "person": cls.vacationer.name,
			"record_key": f"ZUP1:g-test:ОТ:{cls.day}"}).insert(ignore_permissions=True)
		frappe.db.set_value("Person", cls.dismissed.name, "status", "Уволен")

	def test_first_and_last(self):
		from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import execute

		_columns, rows = execute({"date_from": self.day, "date_to": self.day})
		mine = [r for r in rows if r["person"] == self.present.name]
		self.assertEqual(len(mine), 1)
		self.assertEqual(str(mine[0]["first_event"])[:5], "09:00")
		self.assertEqual(str(mine[0]["last_event"])[:5], "18:00")
		self.assertEqual(mine[0]["events_count"], 2)

	def test_absent_hidden_by_default_shown_with_flag(self):
		from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import execute

		filters = {"date_from": self.day, "date_to": self.day}
		_columns, rows = execute(filters)
		self.assertNotIn(self.vacationer.name, {r["person"] for r in rows})
		_columns, rows = execute({**filters, "show_absent": 1})
		mine = [r for r in rows if r["person"] == self.vacationer.name]
		self.assertEqual(mine[0]["absence"], "Отпуск ежегодный")

	def test_dismissed_never_shown(self):
		from access_registry.zkbio.report.zkbio_attendance.zkbio_attendance import execute

		for extra in ({}, {"show_absent": 1}):
			_columns, rows = execute({"date_from": self.day, "date_to": self.day, **extra})
			self.assertNotIn(self.dismissed.name, {r["person"] for r in rows})
```

Хелпер (в этот же файл тестов):

```python
def make_hr_person(last, first, status="Работает"):
	return frappe.get_doc({"doctype": "Person", "last_name": last, "first_name": first,
		"status": status}).insert(ignore_permissions=True)
```

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: реализация**

`zkbio_attendance.py` (`execute(filters=None) -> (columns, rows)`):
- SQL агрегация по `tabZKBio Event`: `person != ''`, `event_time >= %(from)s and < date_add(%(to)s, interval 1 day)`; `group by person, date(event_time)`; `min/max(time(event_time))`, `count(*)`, `group_concat(distinct event_point_name)`.
- Фильтр department: карта person→department по активным `Employment` (status «Работает», missing 0); если фильтр задан — только они; колонка `department` — название из `HR Department.title`.
- Уволенные (`Person.status == "Уволен"`) исключаются в SQL join'ом на `tabPerson`.
- Отсутствие на день: `exists (select 1 from \`tabHR Absence\` a where a.person = e.person and a.cancelled = 0 and a.date_from <= day and coalesce(a.date_to, a.until_expected, a.date_from) >= day)` — подзапросом выбирается `a.state` как `absence`; без `show_absent` строки с непустым absence отбрасываются.
- `person_name` — `Person.full_name` (добрать по образцу diadoc_users.py).

`zkbio_attendance.json`: Script Report, name/report_name «ZKBio Attendance», ref_doctype «ZKBio Event», module «ZKBio», is_standard Yes, roles: System Manager, Registry Admin, Registry Auditor. Workspace: добавить ссылку Report.

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): attendance report with absence exclusion`

---

### Task 6: Отчёт «СКУД: работающие без проходов»

**Files:**
- Create: `access_registry/zkbio/report/zkbio_no_passes/{__init__.py,zkbio_no_passes.json,zkbio_no_passes.py}`
- Modify: workspace `скуд/скуд.json` (ссылка Report «ZKBio No Passes»)
- Test: `access_registry/tests/test_zkbio.py` (TestZKBioNoPassesReport)

**Interfaces:**
- Consumes: Task 5 (фильтры, карта подразделений), `Person`, `Employment`, `HR Absence`, `ZKBio Event`, `ZKBio Person`.
- Produces: `execute(filters)`; filters: `date_from`, `date_to` (reqd), `department`. Колонки: `person_name`, `person` (Link), `department`, `has_card` (Check — есть ли привязанный `ZKBio Person`), `absence_days` (Int — дни отсутствий в периоде), `last_event` (Datetime — последний проход за всё время).

- [ ] **Step 1: failing test**

Фикстура (свой сервер ТСКУДБ, `make_hr_person`/`person_row`/`fetch_with` из Task 5/3):
трое работающих — «Безкарта» (нет `ZKBio Person` и событий), «Ссобытием» (событие вчера),
«Вотпуске» (событий нет, `HR Absence` покрывает весь период date_from..date_to теста).

```python
class TestZKBioNoPassesReport(FrappeTestCase):
	def test_shows_only_workers_without_events(self):
		from access_registry.zkbio.report.zkbio_no_passes.zkbio_no_passes import execute

		_columns, rows = execute({"date_from": cls.from_, "date_to": cls.to})
		persons = {r["person"] for r in rows}
		self.assertIn(cls.no_card.name, persons)          # работает, событий нет, карты нет
		self.assertNotIn(cls.with_events.name, persons)   # события были
		self.assertNotIn(cls.on_leave.name, persons)      # отпуск на весь период
		mine = [r for r in rows if r["person"] == cls.no_card.name][0]
		self.assertEqual(mine["has_card"], 0)
		self.assertEqual(mine["absence_days"], 0)
```

(имена `cls.*` и границы периода выставляет setUpClass; события «Ссобытия» — через `run_server_sync(..., "events")` как в Task 5)

- [ ] **Step 2: run — FAIL**

- [ ] **Step 3: реализация**

`zkbio_no_passes.py`: база — `Person` со `status = "Работает"`; anti-join к событиям периода (`not exists (select 1 from tabZKBio Event e where e.person = p.name and e.event_time в периоде)`); исключить полностью покрытых отсутствиями (число дней периода, покрытых `HR Absence`, < числа дней периода — считать пересечение по датам в Python, людей немного); `has_card` — exists `ZKBio Person` с этим person и `missing_in_source = 0`; `last_event` — `select max(event_time)` отдельным запросом по выбранным person; department-фильтр как в Task 5.
JSON и workspace — по образцу Task 5.

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): report of workers without passages`

---

### Task 7: Документация, линт, полный прогон

**Files:**
- Modify: `docs/architecture.md` (слой «4c. СКУД (ZKBio CVAccess)»: схема, DocTypes, привязка только по ФИО, белый список кодов, watermark, хранение 180 дней)
- Modify: `README.md` (раздел «СКУД»: SQL создания read-only PG-роли, настройка сервера, кнопки, расписание, отчёты, «Перечитать за N дней»)
- Modify: `docs/deployment.md` (psycopg2-binary → пересборка образа)

- [ ] **Step 1: README-фрагмент с SQL для эксплуатации**

```sql
create role registry_ro login password '…';
grant connect on database "biosecurity-boot" to registry_ro;
grant usage on schema public to registry_ro;
grant select on pers_person, auth_department, acc_transaction to registry_ro;
alter role registry_ro set default_transaction_read_only = on;
```

- [ ] **Step 2: `ruff check access_registry` и `ruff format --check` — чисто**

- [ ] **Step 3: полный прогон** `bench --site <site> run-tests --app access_registry` — зелёный (включая `test_scheduler_names`, `test_workspace`)

- [ ] **Step 4: `git status`** — нет мусорных файлов; **Commit** `docs(zkbio): СКУД layer in architecture and README`

- [ ] **Step 5: этап внедрения (вне кода, записать в README как чек-лист):** создать PG-роль → завести `ZKBio Server` → «Проверить подключение» → «Синхронизировать сейчас» → проверить привязку в списке `ZKBio Person` (дубли ФИО — `manual_person`) → сверить первый отчёт со старым журналом СКУД за вчерашний день.
