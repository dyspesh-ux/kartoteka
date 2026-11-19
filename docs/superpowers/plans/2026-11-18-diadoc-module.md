# Модуль «Диадок» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Новый источник реестра «Диадок»: ручная загрузка xlsx-выгрузок (по одной организации на файл или общий реестр), привязка учёток к сотрудникам, права как Entitlement в ролевой модели, напоминания о просроченной загрузке, раздел в `/registry`.

**Architecture:** Модуль `access_registry/diadoc/` по образцу `file_shares`: DocTypes `Diadoc Organization`/`Diadoc User`, парсер xlsx → синк в одной транзакции с savepoint и предохранителем → Sync Log вида «Диадок». Привязка к `Person` — по образцу `access_catalog/linking.py` (вручную → почта к AD → почта к Битрикс24 → ФИО). Ролевая модель — ключи `dd:<орг>|<право>` в `access_roles/engine.py`. Напоминания — новое событие в `notify.py`. UI — Desk-кнопка по образцу `FileServer.load_file` + раздел `/registry`.

**Tech Stack:** Frappe v15 (DocType, whitelisted methods, Query Report), openpyxl (зависимость Frappe), чистый JS без сборки (`www/registry.js`).

**Spec:** `docs/superpowers/specs/2026-11-18-diadoc-module-design.md` (читать вместе с планом).

## Global Constraints

- Python 3.10+, Ruff: длина строки 110, **табы**, двойные кавычки (`pyproject.toml`); код и комментарии — на английском, тексты интерфейса и сообщения пользователю — на русском.
- Перед коммитом: `ruff check access_registry` и `ruff format --check access_registry`.
- Тесты: `bench --site <site> run-tests --app access_registry --module access_registry.tests.test_diadoc` (без bench/MariaDB не запускаются; CI — `.github/workflows/ci.yml`).
- Изменяющие действия — только POST; каждый метод API сам проверяет права (`permissions.require`); личные данные не в Sync Log; источники только читаются (в Диадок не пишем никогда).
- Имена записей источника: `<код>:<идентификатор>`; сохранение только при реальных изменениях (хеш); пропавшие помечаются, не удаляются; каждый запуск — в Sync Log.
- Любой новый справочник — со ссылкой в workspace (проверяет `test_workspace.py`); workspaces пересобираются из JSON при migrate.
- Текущая ветка: `claude/new-session-dkdpyd`.
- Загрузка реализуется whitelisted-методом контроллера `DiadocOrganization.load_file()` по образцу `FileServer.load_file` (file_shares/doctype/file_server/file_server.py:23) — это и есть «endpoint загрузки» из дизайна; отдельный `diadoc/api.py` не создаём (YAGNI: специалист грузит руками с карточки).

## Review Focus

1. **Общий реестр на десятки организаций (первичная загрузка)** — организации автосоздаются, предохранитель не мешает, пропавшие помечаются только среди организаций файла → тесты Task 4.
2. **Обрезанный файл маленькой организации** — предохранитель Диадок срабатывает с 3 записей (глобальный минимум 20 для него не подходит) → тесты Task 4.
3. **Учётка с логином «—»** — ключ по ФИО, повторная загрузка не дублирует, привязка только по ФИО → тесты Task 2 и 3.
4. **Повторная загрузка без изменений** — `src_hash`, версий нет; `manual_person` загрузкой не перезаписывается → тесты Task 4.
5. **Уволенный с активной учёткой / заблокированный с правом «Подпись»** — первый попадает в «Доступ у неработающих», второй не считается в «есть» → тесты Task 6 и 8.

---

### Task 1: Скелет модуля, DocTypes, роль, workspace

**Files:**
- Create: `access_registry/diadoc/__init__.py`
- Create: `access_registry/diadoc/doctype/__init__.py`, `access_registry/diadoc/doctype/diadoc_organization/__init__.py`, `access_registry/diadoc/doctype/diadoc_user/__init__.py`
- Create: `access_registry/diadoc/doctype/diadoc_organization/diadoc_organization.json`
- Create: `access_registry/diadoc/doctype/diadoc_user/diadoc_user.json`
- Create: `access_registry/diadoc/doctype/diadoc_organization/diadoc_organization.py`
- Create: `access_registry/diadoc/doctype/diadoc_user/diadoc_user.py`
- Create: `access_registry/access_registry/workspace/diadoc/diadoc.json`
- Modify: `access_registry/modules.txt` (добавить `Diadoc`)
- Modify: `access_registry/install.py:7-15` (`REGISTRY_ROLES`)
- Modify: `access_registry/permissions.py:8-11` (константа роли)
- Test: `access_registry/tests/test_diadoc.py`

**Interfaces:**
- Produces: DocTypes `Diadoc Organization` (name — название из файла, без autoname; поля `title` Data label «Название», `enabled` Check default 1 label «Напоминать о загрузке», `upload_file` Attach label «Файл выгрузки», `upload_reminder_days` Int default 30 label «Напоминать после, дней», `last_upload` Datetime read_only label «Последняя успешная загрузка», `last_status` Data read_only label «Статус последней загрузки», `users_count`/`linked_count` Int read_only; `track_changes: 1`; права: System Manager, Registry Admin — полные, Registry Auditor + «Diadoc Specialist» — чтение) и `Diadoc User` (без autoname; поля `organization` Link options «Diadoc Organization» reqd, `full_name` Data in_list_view, `position` Data, `login` Data, флаги Check: `login_active`, `is_admin`, `blocked`, `can_sign`, `gov_key`, `can_approve`, `can_request_approve`, `drafts`, `can_delete`, `payments`, `counterparties`, секция «Сотрудник»: `person` Link Person, `person_link_method` Data read_only label «Как найден сотрудник», `person_link_note` Data read_only label «Почему не найден», `manual_person` Link Person label «Сотрудник вручную»; `src_hash` Data read_only, `missing_in_source` Check label «Нет в выгрузке», `uid` Data; `track_changes: 1`; права как у организации + «Diadoc Specialist» — чтение). Роль `Diadoc Specialist` (desk_access=1) с описанием «Специалист по ЭДО Диадок: загружает выгрузки, читает модуль». Workspace `Diadoc` (parent_page «Access Registry», module «Access Registry», роли как у workspace «Общие папки») со ссылками на оба DocType.

- [ ] **Step 1: Написать падающий тест**

В `access_registry/tests/test_diadoc.py` (по конвенции `test_file_shares.py`, `FrappeTestCase`):

```python
def test_module_scaffold():
    assert "Diadoc" in frappe.get_installed_apps_doctypes…  # фактические проверки:
    # frappe.db.exists("DocType", "Diadoc Organization")
    # frappe.db.exists("DocType", "Diadoc User")
    # frappe.db.exists("Role", "Diadoc Specialist")
    # frappe.db.exists("Workspace", "Diadoc")
    # workspace содержит ссылки link_to на оба DocType
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `bench --site <site> run-tests --app access_registry --module access_registry.tests.test_diadoc`
Expected: FAIL (DocType «Diadoc Organization» не существует)

- [ ] **Step 3: Реализовать**

Создать файлы по списке выше. JSON DocType — по образцу `file_shares/doctype/file_server/file_server.json` (табы, двойные кавычки). Контроллеры — минимальные `Document`-наследники с docstring на английском; `validate` только для `Diadoc User`: если задан `manual_person`, установить `person = manual_person`, `person_link_method = "Вручную"` (по образцу контроллера `IB User` — найти через Grep `manual_person` в `access_registry/access_catalog/doctype/ib_user/ib_user.py`). В `install.py` добавить роль в `REGISTRY_ROLES` (после «1C Sync»: `"Diadoc Specialist": "Специалист по ЭДО Диадок: загружает выгрузки, читает модуль"`). В `permissions.py` добавить `DD_SPECIALIST = "Diadoc Specialist"`. Запустить `bench --site <site> migrate` для импорта DocType.

- [ ] **Step 4: Запустить — PASS**, затем `ruff check access_registry && ruff format --check access_registry`

- [ ] **Step 5: Commit**

```bash
git add access_registry/diadoc access_registry/modules.txt access_registry/install.py access_registry/permissions.py access_registry/access_registry/workspace/diadoc access_registry/tests/test_diadoc.py
git commit -m "feat(diadoc): module scaffold, DocTypes, role, workspace"
```

---

### Task 2: Парсер xlsx

**Files:**
- Create: `access_registry/diadoc/parser.py`
- Test: `access_registry/tests/test_diadoc.py` (добавить)

**Interfaces:**
- Produces: `class ParseError(Exception)` (по образцу `file_shares/synology.py`). `parse(data: bytes) -> dict` — возвращает `{"orgs": [str, ...], "users": [ {org: str, full_name: str, position: str, login: str, rights: {flag: 0|1}}, ... ]}`; порядок — порядок первого появления. Ключи `rights` (ровно эти имена): `login_active`, `is_admin`, `blocked`, `can_sign`, `gov_key`, `can_approve`, `can_request_approve`, `drafts`, `can_delete`, `payments`, `counterparties`. Логин «—»/пусто → `login: ""`. `HEADER_ALIASES: dict[str, str]` — нормализованный заголовок → имя поля; нормализация: lower, strip, схлопывание пробелов, удаление точек («Логин акт.» → «логин акт»).

- [ ] **Step 1: Написать падающие тесты**

Тесты строят xlsx в памяти (openpyxl в `BytesIO`), по аналогии с фикстурами `test_file_shares.py`. Минимум:
- `test_parse_finds_header_not_in_first_row` — пустая строка 1, шапка на строке 2, данные со строки 3;
- `test_parse_booleans_and_legend_ignored` — значения «True»/«False» строками, колонки R:S с легендой не мешают;
- `test_parse_login_dash_becomes_empty`;
- `test_parse_multi_org_and_duplicates` — две организации, дубликат логина → последняя строка, `orgs` без дублей;
- `test_parse_bad_file_raises` — нет колонки «ФИО» → `ParseError`.

- [ ] **Step 2: Запустить — FAIL** (`--module access_registry.tests.test_diadoc`, фильтр по имени теста через `-t` нет в bench — запускается весь модуль)

- [ ] **Step 3: Реализовать `parse` в `access_registry/diadoc/parser.py`**

openpyxl `load_workbook(BytesIO(data), read_only=True, data_only=True)`. Перебор первых 10 строк: строка, содержащая заголовок «фио» после нормализации — шапка. Соответствие колонок — по `HEADER_ALIASES` для всех 15 колонок; обязательные: Компания, ФИО; остальные при отсутствии колонки → значение по умолчанию (пусто/0) + предупреждение в возвращаемом `warnings`? Нет — интерфейс фиксирован: без `warnings`; нераспознанные колонки прав игнорируются, отсутствие обязательных («Компания», «ФИО») → `ParseError`. bool-парсер: истина ∈ {true, 1, да, истина, y, yes} (lower/strip), иначе ложь.

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): xlsx parser for the registry export format`

---

### Task 3: Привязка к сотруднику

**Files:**
- Create: `access_registry/diadoc/linking.py`
- Test: `access_registry/tests/test_diadoc.py` (добавить)

**Interfaces:**
- Consumes: `NameMatcher` из `access_registry/access_catalog/linking.py:23` (метод `match(full_name) -> (person|None, note)`).
- Produces: константы методов `BY_EMAIL_AD = "Учётка AD (почта)"`, `BY_EMAIL_B24 = "Битрикс24 (почта)"`, `BY_NAME = "ФИО"`; класс `DiadocResolver` с методом `resolve(full_name: str, login: str) -> tuple[str | None, str, str]` — возвращает `(person, method, note)`; note объясняет неудачу (тексты как в `linking.py`).

- [ ] **Step 1: Написать падающие тесты**

Фикстуры: `Person` (ФИО «Иванов Иван Иванович»), `AD Account` с `user_principal_name = "iivanov@corp.local"` и привязанным person, `B24 User` с email и person. Тесты:
- логин = UPN учётки AD → person этой учётки, метод BY_EMAIL_AD;
- логин = email пользователя Битрикс24 → person, BY_EMAIL_B24;
- логин пустой, ФИО совпадает → BY_NAME;
- ничего не найдено → `(None, "", "сотрудник с ФИО «…» не найден)"` (текст note как у `NameMatcher`);
- КАПС-ФИО совпадает с обычным (NameMatcher это уже покрывает — один тест на регрессию).

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `DiadocResolver`**

Индексы в `__init__`: UPN→person из `tabAD Account` (`missing_in_source=0`, person задан, `user_principal_name` непуст — lower); email→person из `tabB24 User` (`active=1`, `missing_in_source=0`); `self.names = NameMatcher()`. `resolve`: если `login` содержит «@» — сначала AD-индекс, потом B24; иначе/далее `self.names.match(full_name)`.

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): person resolution by AD mail, B24 mail, full name`

---

### Task 4: Синх загрузки

**Files:**
- Create: `access_registry/diadoc/sync.py`
- Modify: `access_registry/access_registry/doctype/sync_log/sync_log.json` (опции `kind` — добавить `Диадок`)
- Test: `access_registry/tests/test_diadoc.py` (добавить)

**Interfaces:**
- Consumes: `parse` (Task 2), `DiadocResolver` (Task 3), `save_changed`/`src_hash` из `access_registry/access_catalog/importer.py`, `_switch_user`/`_format_messages` из `access_registry/sync/engine.py`, `org_key` — см. ниже, `refresh_counters` из `access_registry/access_roles/engine.py:669`.
- Produces: `GUARD_MIN_RECORDS = 3` (module const), `org_key(title: str) -> str` (lower, ё→е, кавычки «»“”‘’ → `"`, схлопывание пробелов — для сопоставления организаций), `user_key(org_name: str, login: str, full_name: str) -> str` (`f"{org}:{login}"` если логин, иначе `f"{org}:фио:{normalize_name(full_name)}"`; `normalize_name` из `access_registry/sync/normalize.py`), `run_upload(data: bytes, commit: bool = True)` — парсит, синкает в savepoint, пишет Sync Log (kind «Диадок»), обновляет `last_upload`/`last_status`/`users_count`/`linked_count` организаций из файла, вызывает `engine.refresh_counters()`, возвращает doc Sync Log. Класс `DiadocGuardTripped(frappe.ValidationError)`.

- [ ] **Step 1: Написать падающие тесты**

Каждый тест строит xlsx (helper из Task 2) и зовёт `run_upload(bytes, commit=False)`. Тесты:
- `test_first_load_creates_orgs_and_users` — общий файл с 2 организациями: обе организации созданы, пользователи созданы, `last_upload` проставлен, Sync Log kind «Диадок», status «Успех»;
- `test_reload_unchanged_creates_no_versions` — повторная загрузка того же файла: счётчик «без изменений», у `Diadoc User` нет новых `Version`;
- `test_missing_marked_only_for_orgs_in_file` — загрузить файл с орг A+B, потом файл только с орг A, где одного пользователя нет: у A он `missing_in_source=1`, у B записи без изменений;
- `test_guard_trips_on_shrunk_file` — в реестре 5 пользователей орга (4 созданы + 1 добит отдельной мелкой загрузкой того же орга), файл с 2 → `DiadocGuardTripped`, Sync Log «Остановлен предохранителем», изменений в реестре нет;
- `test_manual_person_survives_reload` — `manual_person` задан → после перезагрузки сохранён, `person` не сброшен;
- `test_person_linking_applied` — пользователь с логином учётки AD привязался с методом BY_EMAIL_AD; в Sync Log messages нет ФИО (проверить отсутствие строки с фамилией в `log.messages`).

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `run_upload` в `access_registry/diadoc/sync.py`**

По структуре `file_shares/sync.py::run_upload`: Sync Log → `_switch_user` → savepoint → try: `parse` → `DiadocImport(...).run(parsed)` → «Успех»; except `DiadocGuardTripped`/`ParseError`/`Exception` → rollback(savepoint), статус и traceback как у file_shares. Класс `DiadocImport` по образцу `ShareImport`/`SnapshotImport`: `load`-хелпер через `frappe.db.exists` + сравнение `src_hash` (payload: `{**user, "person": [...], "v": 1}`); upsert организаций (сопоставление существующих по `org_key(name)`; создание: `doc.name = title из файла`, `title` = то же); upsert пользователей (`uid` = `user_key`); пометка пропавших — только `organization in orgs_of_file`; предохранитель **по каждой** организации файла: `actual = count(Diadoc User, organization=org, missing_in_source=0)`, если `actual >= GUARD_MIN_RECORDS` и `received < actual * (1 - threshold/100)` → `DiadocGuardTripped` (текст — по образцу ShareGuardTripped, с подсказкой про `shrink_threshold_pct`). В статистику: created/updated/unchanged/missing/guard. В конце успеха — `refresh_counters()` и обновление счётчиков организаций.

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): upload sync with per-org guard and Sync Log`

---

### Task 5: Кнопка загрузки на карточке и права

**Files:**
- Create: `access_registry/diadoc/doctype/diadoc_organization/diadoc_organization.js`
- Modify: `access_registry/diadoc/doctype/diadoc_organization/diadoc_organization.py`
- Modify: `access_registry/diadoc/doctype/diadoc_organization/diadoc_organization.json` и `diadoc_user.json` (роль «Diadoc Specialist» — write для организации)
- Test: `access_registry/tests/test_diadoc.py` (добавить)

**Interfaces:**
- Consumes: `run_upload` (Task 4), `DD_SPECIALIST` (Task 1).
- Produces: `DiadocOrganization.load_file()` — whitelisted, как `FileServer.load_file` (file_server.py:23): `require(*ADMINS, DD_SPECIALIST)` (вызов разрешён специалисту — отличие от FileServer), читает вложение из `upload_file` через `frappe.get_doc("File", {"file_url": self.upload_file}).get_content()`, `run_upload(content, commit=not frappe.flags.in_test)`, возвращает `{"status": log.status, "log": log.name, "stats": frappe.parse_json(log.stats or "{}")}`. JS: кнопка «Загрузить выгрузку» по образцу `file_shares/doctype/file_server/file_server.js` (frm.call + frappe.msgprint с результатом).

- [ ] **Step 1: Написать падающие тесты**

- `test_load_file_requires_role` — пользователь с ролью «Diadoc Specialist» без админских ролей: `load_file` проходит (mock `run_upload` через `unittest.mock.patch`, файл приложен); пользователь без роли и без прав — `frappe.PermissionError`;
- `test_load_file_without_attachment_throws` — понятное сообщение «Приложите файл выгрузки…».

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): manual upload button and specialist permissions`

---

### Task 6: Ролевая модель — Entitlement и engine

**Files:**
- Modify: `access_registry/access_roles/doctype/entitlement/entitlement.json`
- Modify: `access_registry/access_roles/engine.py`
- Test: `access_registry/tests/test_diadoc.py` (добавить; по образцу `test_roles.py`/`test_roles_catalog.py`)

**Interfaces:**
- Produces: в `Entitlement` — опция «Диадок» в `system`, поля `dd_organization` (Link, options «Diadoc Organization», label «Диадок: организация», description «Пусто — любая организация») и `dd_right` (Select, label «Диадок: право», options — ровно: `Подпись\nГосключ\nСогл.\nЗапрос согл.\nЧерновики\nУдаление\nПлатежки\nКонтрагенты\nАдмин`). Константа модуля в `engine.py`: `DD_RIGHTS = ("Подпись", "Госключ", "Согл.", "Запрос согл.", "Черновики", "Удаление", "Платежки", "Контрагенты", "Админ")` и маппинг флаг→право: `DD_FLAG_RIGHT = {"can_sign": "Подпись", "gov_key": "Госключ", "can_approve": "Согл.", "can_request_approve": "Запрос согл.", "drafts": "Черновики", "can_delete": "Удаление", "payments": "Платежки", "counterparties": "Контрагенты", "is_admin": "Админ"}`. Ключ: `dd:<organization>|<право>`; права считаются только у `login_active=1`, `blocked=0`, `missing_in_source=0`, `person` задан.

- [ ] **Step 1: Написать падающие тесты**

Фикстура: 2 организации, по пользователю в каждой, один с `can_sign=1, blocked=1` (не считается), один с `can_sign=1, login_active=0` (не считается), один активный с `is_admin=1`. Тесты:
- `test_raw_accesses_dd_keys` — `engine.raw_accesses()` содержит `dd:ОРГ1|Подпись` для активного подписанта, не содержит для заблокированного/неактивного; `dd:ОРГ2|Админ` присутствует с evidence «Диадок …»;
- `test_entitlement_dd_any_org` — `Entitlement` с `system="Диадок"`, `dd_right="Подпись"`, пустой `dd_organization` покрывает ключ обеих организаций (через `engine.entitlement_keys()`); с заполненной — только своей;
- `test_describe_key_dd_admin_privileged` — `engine.describe_key("dd:ОРГ2|Админ")` → `{"title": "Диадок ОРГ2: Админ", "system": "Диадок", "dd_organization": "ОРГ2", "dd_right": "Админ", "privileged": 1}`; для «Подпись» ключа `privileged` = 0;
- `test_reconcile_counts_dd` — сверка: у сотрудника с не каталогизированным `dd:`-ключом строка попадает в uncatalogued (через `actual_entitlements`).

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать**

`engine.py`: ветка `dd:` в `describe_key` (парсинг `ref.split("|", 1)`; `privileged=1` если право «Админ», иначе 0 — добавить ключ `privileged` в возвращаемый dict; проверить, что `ensure_entitlement` это переживает: он ставит `privileged` по `PRIVILEGED_WORDS` — оставить его логику, но для dd подставить явный флаг, например `values.pop("privileged")`-стиль — аккуратно, не сломав другие ветки: проще в `ensure_entitlement` не менять ничего, а `privileged` учитывать в `describe_key` только для dd; в `ensure_entitlement` добавить `privileged=int(values.pop("privileged", any(word in title.lower() ...)))`); SQL в `raw_accesses` — один запрос по `tabDiadoc User` с динамическими колонками флагов (или простой выбор нужных колонок и цикл по `DD_FLAG_RIGHT`); в `entitlement_keys` — для `dd_right` без организации перебрать все `Diadoc Organization`, иначе одну (по образцу ветки `bit` с пустым `bit_base`).

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): entitlements and reconciliation keys dd:<org>|<right>`

---

### Task 7: Напоминания о загрузке

**Files:**
- Modify: `access_registry/notify.py` (константа события, `_diadoc_items`, `COLLECTORS`, `_app_link`)
- Modify: `access_registry/access_registry/doctype/registry_notification_rule/registry_notification_rule.json` (опция события)
- Test: `access_registry/tests/test_notify.py` (добавить; по образцу существующих тестов событий)

**Interfaces:**
- Consumes: `DD_SPECIALIST` (Task 1), поля `Diadoc Organization` (Task 1).
- Produces: константа `DIADOC = "Диадок: пора загружать выгрузку"` (notify.py, рядом с `SOURCES`), коллектор `_diadoc_items(rule) -> list[Item]` — элементы: `key=f"diadoc|{org.name}"`, `text=f"{org.title or org.name}: последняя загрузка {дата}"` или «…: ещё не загружалась», `group="Диадок"`, `url=get_url("/app/diadoc-organization")`, `visible=lambda user, a: frappe.has_permission("Diadoc Organization", ptype="read", user=user)`; запись в маппинге `_app_link`.

- [ ] **Step 1: Написать падающие тесты**

- организация без `last_upload` → элемент «ещё не загружалась»;
- `last_upload` 40 дней назад, `upload_reminder_days=30` → элемент есть; 10 дней назад → нет;
- `visible`: пользователь с ролью «Diadoc Specialist» видит; пользователь без прав на DocType — нет; админ — видит;
- `run(rule, send=False)` возвращает элементы получателю-специалисту (правило с этим событием).

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): stale-upload reminder notification event`

---

### Task 8: Контрольные списки и счётчики учёток

**Files:**
- Modify: `access_registry/registry/api.py` (`Accounts`, `people()`, `person()`, `_control_dismissed`, `_control_unlinked`)
- Modify: `access_registry/registry/metrics.py` (`SYSTEMS` — добавить `"Диадок": "dd"`, если ключ используется; проверить Grep'ом `SYSTEMS` в файле)
- Test: `access_registry/tests/test_registry_control.py` (добавить; по образцу существующих)

**Interfaces:**
- Produces: `Accounts` получает `self.dd` (active = `login_active=1 and blocked=0 and missing_in_source=0`) и `"Диадок"` в `active()`; `people()` — колонка `dd` и labels `{"1С": "1c", "AD": "ad", "Битрикс24": "b24", "Диадок": "dd"}`; `person()` — ключ `"dd"` в ответе: список `Diadoc User` по человеку (`organization, full_name, login, login_active, is_admin, blocked, can_sign, gov_key, can_approve, can_request_approve, drafts, can_delete, payments, counterparties, missing_in_source, person_link_method`), отдаётся если `"dd" in allowed`; `_control_dismissed` — ветка union: `select p.name, p.full_name, p.status, 'Диадок', concat(d.organization, ': ', d.full_name), d.name, 'Diadoc User', null from tabDiadoc User d join tabPerson p ... where p.status != 'Работает' and d.login_active = 1 and d.blocked = 0 and d.missing_in_source = 0`; `_control_unlinked` — ветка union: `'Диадок', concat(organization, ': ', full_name), ifnull(login, ''), person_link_note, name, 'Diadoc User'` где `login_active=1 and blocked=0 and missing_in_source=0 and ifnull(person,'')=''` (технико-учётки Entera EDI будут здесь — как и техучётки других систем).

- [ ] **Step 1: Написать падающие тесты**

- `test_dismissed_includes_active_diadoc` — уволенный с `login_active=1, blocked=0` в списке; заблокированный — нет;
- `test_unlinked_includes_diadoc` — активный без person в списке;
- `test_people_flags_dd` — `people()` возвращает `dd`-счётчик и флаг `dismissed_access` по активной dd-учётке (по образцу существующих тестов people);
- `test_person_includes_dd` — `person()` содержит `dd`-блок с флагами, и пустой при отсутствии права на систему `dd` (если тестовая инфраструктура позволяет — иначе проверить `_trim_systems`).

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): control lists and account counters`

---

### Task 9: Раздел `/registry`, система доступа, поле профиля

**Files:**
- Create: `access_registry/registry/diadoc_api.py`
- Modify: `access_registry/registry/app_access.py` (`SECTIONS`, `SYSTEMS`, `SYSTEM_OF_LABEL`)
- Modify: `access_registry/access_registry/doctype/registry_access_profile/registry_access_profile.json` (поле `sys_dd` по образцу `sys_shares`)
- Modify: `access_registry/registry/api.py` — только если импорт/маршрутизация нового модуля требует (по образцу, как подключены другие разделы; Grep `import` разделов в `registry/api.py`)
- Modify: `access_registry/www/registry.js` (`MGMT_SYSTEMS`, пункт меню/страница раздела по образцу существующих разделов, блок учёток в карточке 360°)
- Test: `access_registry/tests/test_app_access.py`, `access_registry/tests/test_registry_app.py` (добавить)

**Interfaces:**
- Produces: whitelisted `diadoc_organizations() -> list[dict]` — `[{name, title, last_upload, last_status, users_count, linked_count, overdue: bool}]` (`overdue`: `last_upload` пусто или старше `upload_reminder_days`; сортировка по `title`); whitelisted `diadoc_users(organization: str) -> list[dict]` — пользователи организации с флагами, `person`, `full_name`, `person_link_method`, `missing_in_source`. Оба: `_check("diadoc")` + `aa.system_allowed("dd")`. `app_access`: `SECTIONS["diadoc"] = "Диадок"`, `SYSTEMS["dd"] = "Диадок"`, `SYSTEM_OF_LABEL["Диадок"] = "dd"`; в профиле доступа `sys_dd` (Check, label «Диадок», рядом с `sys_shares`). В `registry.js`: `MGMT_SYSTEMS` получает `["dd", "Диадок"]`; страница раздела — таблица организаций с датой загрузки и подсветкой просрочки, клик → пользователи; карточка 360° — блок «Диадок» со списком учёток и флагами.

- [ ] **Step 1: Написать падающие тесты**

- `test_section_and_system_registered` — у роли VIEWER появился раздел `diadoc`, `systems()` содержит `dd`;
- `test_profile_sys_dd` — в `Registry Access Profile` есть поле `sys_dd`, снятие галки убирает `dd` из `systems()` (по образцу тестов `sys_shares`);
- `test_diadoc_organizations_api` — метод возвращает организации с `overdue` (фикстура: одна свежая, одна просроченная, одна без загрузок);
- `test_diadoc_users_api_requires_system` — при недоступной системе `dd` метод не отдаёт данные (по образцу тестов ограничения систем).

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Запустить — PASS**, ruff

- [ ] **Step 5: Commit** — `feat(diadoc): /registry section, access system dd, profile flag`

---

### Task 10: Отчёты, ссылки workspace, документация

**Files:**
- Create: `access_registry/diadoc/report/__init__.py`, `access_registry/diadoc/report/diadoc_users/__init__.py`, `access_registry/diadoc/report/diadoc_users/diadoc_users.{json,py}`, `access_registry/diadoc/report/diadoc_admins/__init__.py`, `access_registry/diadoc/report/diadoc_admins/diadoc_admins.{json,py}`
- Modify: `access_registry/access_registry/workspace/diadoc/diadoc.json` (ссылки на отчёты; карточки-ссылки)
- Modify: `docs/architecture.md` (слой «Диадок» в схеме и раздел «Диадок — ручная загрузка» по образцу «Общие папки Synology»)
- Modify: `README.md` (эксплуатация: роль специалиста, загрузка с карточки, настройка напоминания)
- Test: `access_registry/tests/test_diadoc.py` (добавить), `access_registry/tests/test_workspace.py` (проверка ссылок — дополнить, если он параметризован)

**Interfaces:**
- Produces: Query Reports «Пользователи Диадок» (колонки: Организация, ФИО, Логин, флаги да/нет, Сотрудник, «Как найден», «Нет в выгрузке») и «Диадок: администраторы» (орг, ФИО, логин, активен/заблокирован, сотрудник; фильтр `is_admin=1`, `login_active=1`). JSON отчётов — по образцу `access_registry/file_shares/report/share_access/share_access.json`; `execute(filters=None)` — по образцу `share_access.py`.

- [ ] **Step 1: Написать падающие тесты**

- отчёты существуют и `execute` возвращает колонки/строки по фикстурам; «Пользователи Диадок» не показывает `missing_in_source=1` по умолчанию; «администраторы» — только активные админы;
- workspace «Диадок» содержит ссылки на оба отчёта.

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать**

- [ ] **Step 4: Запустить — PASS** (включая полный прогон `bench --site <site> run-tests --app access_registry`), ruff

- [ ] **Step 5: Commit** — `feat(diadoc): reports, workspace links, docs`

---

## Self-review (заполнено автором плана)

- Покрытие спека: модуль/DocTypes — Task 1; парсер — 2; привязка — 3; синк+предохранитель+Sync Log+пересчёт — 4; кнопка и права — 5; Entitlement/engine — 6; напоминания — 7; контрольные списки/счётчики — 8; `/registry`/доступ/профиль — 9; отчёты/workspace/документация — 10. Пункт «даджест не трогаем» — осознанно без задачи (решение спека).
- Типы согласованы: `parse`, `DiadocResolver.resolve`, `run_upload`, `org_key`, `user_key`, `DD_RIGHTS`/`DD_FLAG_RIGHT`, `DIADOC`, `dd`-ключи используются в последующих задачах теми же именами.
- Review Focus (5 шт.) привязаны к тестам: 1,2,4 → Task 4; 3 → Task 2/3; 5 → Task 6/8.
- Пропорция: план ~4 страницы при спеке ~5 — без транскрипции тел функций (тела даются только там, где сигнатуры не определяют решение: парсер, engine-ветки).
