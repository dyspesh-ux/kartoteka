# Дизайн: слой СКУД (ZKBio CVAccess) — приходы/уходы и отчёты по проходам

Дата: 2026-11-25
Статус: на согласовании (builder check пройден автором — субагент недоступен в этой сессии; блокирующих вопросов к дизайну не выявлено, открытые неопределённости вынесены в «Допущения»)

## Зачем

В реестре есть кадры (ЗУП), учётки (AD, 1С, Битрикс24) и доступы, но нет факта физического
присутствия: когда человек реально пришёл и ушёл. Источник — СКУД ZKBio CVAccess 4.0.2_R,
её база PostgreSQL (`biosecurity-boot`, порт 5442, read-only доступ). Реестр тянет события
проходов и персонал СКУД, привязывает к сотрудникам и даёт отчёт «приход/уход», в котором
сотрудники в отпуске или командировке (по `HR Absence` из ЗУП) исключаются или подсвечиваются.

## Схема источника (подтверждена дампом `zkbio_schema.sql` от 2026-11-25)

- `pers_person` — персонал: `id` (varchar 50, стабильный), `pin` (табельный номер),
  `last_name`, `name`, `email`, `auth_dept_id` → `auth_department.id`, `hire_date`,
  `status` (smallint), `update_time`. Поле `birthday` есть, но **не забираем**
  (инвариант 3: лишние персональные данные не копируем).
- `acc_transaction` — события: `id`, `event_time` (timestamp without time zone, локальное
  время сервера СКУД), `pin`, `last_name`/`name`, `card_no`, `dev_alias`, `dev_id`,
  `event_point_id`/`event_point_name`, `reader_name`, `event_no` (smallint, код события),
  `event_name`, `verify_mode_name`, `acc_zone`, `area_name`, `dept_name`.
  Индексы по `event_time`, `create_time`, `update_time` есть — инкрементальная выборка
  по watermark быстрая.
- `acc_door` / `acc_reader` — двери и считыватели (названия уже денормализованы в
  `acc_transaction`, отдельно не зеркалим).
- `pers_card` — карты (`card_no`, `person_pin`, `card_state`); нужна для отчёта
  «чужая/непривязанная карта» — этап 2, в этап 1 не входит.

Направление прохода в событиях не хранится: приход = первое событие дня, уход = последнее.
Если вход и выход — разные турникеты, уточнение по `event_point_name`/`acc_zone` — этап 2.

## Цели / не-цели

Цели (этап 1):

- зеркало персонала СКУД (`ZKBio Person`) и событий проходов (`ZKBio Event`), только чтение;
- привязка персонала СКУД к `Person` (табельный номер → ФИО, ручное переопределение);
- Query Report «СКУД: приходы и уходы» (период, подразделение; первое/последнее событие
  дня, число событий; исключение/подсветка отсутствующих по `HR Absence`);
- Query Report «СКУД: работающие без проходов» (кто должен быть, но событий нет);
- Sync Log вида «СКУД», предохранитель, пометка пропавших — по общим правилам источников.

Не-цели (этап 1):

- запись в ZKBio (инвариант 1) — никогда;
- учёт прав доступа СКУД (`acc_level`, зоны) как Entitlement — отдельная история;
- табельный учёт и опоздания по графику (в реестре нет графиков работы);
- фото, биометрия, `pers_card`, раздел в `/registry`, карточка 360° — этап 2+;
- автоопределение направления по турникетам.

Анти-цели: падение синка при недоступности СКУД не должно затрагивать другие источники;
ФИО в сообщениях Sync Log; события без ключа и дубликаты при повторной выборке.

## Подходы

Выбран вариант **A — pull** (решение заказчика, 2026-11-25): Frappe сам читает PostgreSQL
по расписанию, как AD. Альтернатива B (push-скрипт на сервере СКУД, как Synology) отложена:
нужна, только если сетевой доступ с сервера Frappe на СКУД закроют.

## Решения по устройству

### Модуль и DocTypes

Новый модуль Frappe `ZKBio` (`access_registry/zkbio/`, добавить в `modules.txt`).

- `ZKBio Server` — сервер СКУД (по образцу `AD Domain`): `name` = код (`SKD1`),
  `enabled`, `host`, `port` (default 5442), `database` (default `biosecurity-boot`),
  `username`, `password` (Password), `pass_event_nos` (коды событий-проходов через
  запятую, default «0»), `events_overlap_minutes` (перекрытие окна событий,
  default 10), `persons_fetch` и `events_watermark` (read-only, служебные), `last_sync`,
  `last_status`. Кнопки «Проверить подключение» (select 1 + список/число строк
  `pers_person`, `acc_transaction`) и «Синхронизировать сейчас» (обе фазы).
- `ZKBio Person` — зеркало `pers_person`. Имя (ключ): `<код сервера>:<id>`.
  Поля: `server` (Link), `zkbio_id`, `pin`, `last_name`, `first_name`, `email`,
  `dept_name` (join `auth_department`), `hire_date`, `status` (smallint как число),
  `src_hash`, `missing_in_source`; привязка: `person`, `person_link_method`,
  `person_link_note`, `manual_person`. `track_changes` = 1.
- `ZKBio Event` — зеркало `acc_transaction`, append-only. Имя (ключ):
  `<код сервера>:<id>`. Поля: `server`, `zkbio_id`, `event_time` (Datetime, как есть —
  локальное время СКУД), `zkbio_person` (Link), `pin`, `person` (Link, денормализовано
  при вставке для отчётов), `card_no`, `dev_alias`, `event_point_name`, `reader_name`,
  `event_no`, `event_name`, `verify_mode_name`, `acc_zone`, `area_name`.
  `track_changes` = 0 (события не версионируются). Индексы: `event_time`,
  `(person, event_time)`. Очистка: `default_log_clearing_doctypes` = 180 дней
  (настраивается в Log Settings, как Sync Log).

Права на все три DocType: System Manager и Registry Admin — полные, Registry Auditor —
чтение (по образцу HR Absence).

### Клиент PostgreSQL (`zkbio/pg_client.py`)

- Драйвер `psycopg2-binary` — добавить в `dependencies` pyproject.toml; импорт ленивый
  внутри функции, чтобы установка приложения без драйвера не падала (ошибка с понятным
  текстом при запуске синка).
- Подключение: `options="-c default_transaction_read_only=on"` + `statement_timeout`
  (60 с), `connect_timeout` 10 с. В базу не пишем никогда; для эксплуатации в README —
  скрипт создания отдельной read-only PG-роли (SELECT на `pers_person`, `auth_department`,
  `acc_transaction`).
- События: `select ... from acc_transaction where event_time > %s order by event_time
  limit %s` с watermark = `max(event_time)` прошлого запуска минус `events_overlap_minutes`;
  дубликаты отбрасываются по ключу `<server>:<id>` (upsert «без изменений»). Персонал:
  полный select `pers_person left join auth_department` (объём сотни записей).

### Синк (`zkbio/sync.py`)

По образцу `active_directory/sync.py`: Sync Log (добавить вид «СКУД» в `Sync Log.kind`),
savepoint, `_switch_user` на пользователя синка, `_format_messages` (redact),
`last_sync`/`last_status` на сервере, `fetch` параметр для тестов.

- Фаза **events**: читает пачками от watermark; при первом запуске (watermark пуст)
  стартует от `today - 180 дней` (глубже хранения всё равно нет). Для каждого события по `pin` находит
  `ZKBio Person` (карта в памяти) и кладёт `zkbio_person` + `person`. Импортируются
  только события с непустым `pin` и `event_no` из белого списка `pass_event_nos`
  (Data-поле на сервере, коды через запятую, default «0»). Проверено по распределению
  2026-11-25 (273 424 события за 15 месяцев): код 0 — 234 691 (86%, обычный проход),
  202 — 31 792 (значение не подтверждено: имена событий в базе не локализованы,
  `acc_eventNo_*`; если выяснится, что это тоже проходы — администратор допишет код
  в настройки), 700 — «устройство отключено», системное. События вне белого списка и
  без `pin` пропускаются со счётчиком. Вставка только новых ключей, обновлений нет
  (append-only). Событие привязывается к тому
  `person`, который известен на момент синка; если персонал появился позже, сироты
  остаются без привязки до перезаливки окна (кнопка на сервере «Перечитать за N дней» —
  сдвигает watermark назад, дубликаты пропускаются).
- Фаза **persons**: полный срез, upsert по хешу, пропавшие → `missing_in_source`,
  предохранитель по общему `shrink_threshold_pct`/`guard_min_records` (считаем активных
  `ZKBio Person` сервера).
- Сообщения Sync Log без ФИО: предупреждения по `pin`/id (инвариант 3).

### Привязка к сотруднику (`ZKBio Person.person`)

Решение заказчика (2026-11-25): **только ФИО**. `pin` храним как данные, но для привязки
не используем (в СКУД он не совпадает с табельным номером ЗУП).

1. `manual_person` — всегда главнее, синк не трогает.
2. **ФИО** через `NameMatcher` (`access_catalog/linking.py`): `last_name + name`; среди
   однофамильцев выигрывает единственный работающий, иначе связи нет.

Способ/причина — в `person_link_method` («ФИО») и `person_link_note`. Непривязанные
видны в отчёте «СКУД: работающие без проходов» и списке `ZKBio Person` (фильтр по
пустому `person`).

### Расписание (hooks.py)

- События: `"10,25,40,55 * * * *"` → `zkbio.sync.scheduled_events_sync`.
- Персонал: `"45 4 * * *"` → `zkbio.sync.scheduled_persons_sync`.
- Имена методов различаются в последних двух частях (`events_sync`/`persons_sync`) —
  покрыто `test_scheduler_names`. Оба метода перебирают `ZKBio Server.enabled=1` и
  ставят по одной long-задаче на сервер (`deduplicate`, `job_id` — по образцу AD).

### Отчёты (Query Report, SQL; регистрируются в workspace)

- «СКУД: приходы и уходы». Фильтры: `date_from`, `date_to` (обязательные), `подразделение`
  (HR Department, опц.), «Показывать отсутствующих» (Check, default 0), «Только без
  привязки» нет — это второй отчёт. Строки: сотрудник, дата, первое событие, последнее
  событие, число событий, точки прохода (group_concat distinct event_point_name),
  отсутствие (state из HR Absence, если есть). Источник: `ZKBio Event` join `ZKBio Person`
  join `Person`; группировка `person + date(event_time)`. Отсутствующий = есть
  `HR Absence` с `cancelled=0`, `person`, и `date` между `date_from` и
  `coalesce(date_to, until_expected, date)`. Уволенные (`Person.status='Уволен'`)
  исключаются всегда. Отчёт читает только события с непустым `person`.
- «СКУД: работающие без проходов». Фильтры: период, подразделение. Сотрудники
  `Person.status='Работает'`, у которых за период нет ни одного события и которые
  не покрыты `HR Absence` на весь период; рядом — число дней отсутствия и последнее
  событие (max event_time за всё время). Отвечает «кто ходит мимо СКУД или карта
  не привязана».

Отчёты — в workspace; выгрузка в CSV — штатная кнопка Query Report.

### Workspace, права, регистрация

- Новый workspace «СКУД»: списки `ZKBio Server`, `ZKBio Person`, `ZKBio Event`, оба отчёта,
  ссылка на Sync Log (пересобирается при migrate через `install.sync_workspace`;
  `test_workspace.py` требует ссылок на все новые DocType).
- Роли новые не нужны: Registry Admin / Registry Auditor покрывают сценарии.
- Дайджест и уведомления не трогаем (этап 1).

### Тесты и инварианты

- Тесты по образцу `test_zup_sync.py`/`test_file_shares.py`: фаза persons (создание,
  обновление по хешу, пропавшие, предохранитель), фаза events (watermark, дубликаты,
  сироты без pin, привязка события к person), привязка ZKBio Person (табельный → ФИО →
  manual выигрывает), SQL отчётов (отпуск/командировка исключаются, уволенный не виден),
  scheduler names, workspace.
- Инварианты: только чтение из PostgreSQL; изменяющие действия — только POST (кнопки
  сервера); ФИО не в Sync Log; записи `<код>:<id>`; пропавшие помечаются; каждый запуск —
  Sync Log, весь синк — в одном savepoint.

### Документация и эксплуатация

- `docs/architecture.md` — слой «4c. СКУД (ZKBio CVAccess)» в схему и раздел по устройству.
- `README.md` — настройка: создание read-only PG-роли (SQL-скрипт), карточка `ZKBio Server`,
  проверка подключения, расписание, смысл отчётов, как перечитать окно событий.
- Деплой: `psycopg2-binary` в pyproject → пересборка образа (`deploy/docker`), отметить
  в `docs/deployment.md`/`docs/docker.md`.

## Допущения (проверить до/при внедрении)

1. `event_time` — локальное время сервера СКУД в той же зоне, что у сервера Frappe;
   храним naive-как-есть, сравнение только внутри СКУД-данных.
2. Привязка только по ФИО (решение заказчика): при дублях ФИО часть персонала останется
   непривязанной до ручной разметки `manual_person`.
3. Белый список проходов по умолчанию — код 0 (подтверждено распределением: 86% событий).
   Смысл кодов 202/208/27/20 не подтверждён (имена не локализованы); при необходимости
   коды добавляются в `pass_event_nos` без доработки кода.
4. `ZKBio Event` без версионирования и с очисткой за 180 дней достаточно для отчётности
   (~600 событий/день, 171 человек → ~110 тыс. строк за период хранения).
5. Раздел в `/registry` не нужен на этапе 1 — хватает Query Reports в Desk.
