# ZKBio Latecomers (отчёт «СКУД: опоздания») Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Отчёт «СКУД: опоздания» с настраиваемым порогом для руководства + инструкция по его авторассылке; симметричные улучшения «СКУД: приходы и уходы».

**Architecture:** Общая SQL-выборка «день × сотрудник» из `zkbio_attendance` выносится в хелпер; новый Script Report `ZKBio Latecomers` добавляет порог и минуты опоздания. Рассылка — штатный Auto Email Report (без кода), работает благодаря умолчаниям фильтров (вчера, 09:00, только опоздавшие).

**Tech Stack:** Frappe v15 Script Reports, MariaDB.

**Spec:** `docs/superpowers/specs/2026-11-25-zkbio-latecomers-design.md`

## Global Constraints

- Python 3.10+, Ruff: строка 110, табы, двойные кавычки; `ruff check` + `ruff format --check` перед коммитом.
- Код и комментарии — на английском; интерфейс и сообщения — на русском.
- Опоздание — строго после порога (first_event > threshold); выходные — WEEKDAY(day) <= 4; уволенные и отсутствующие (HR Absence) исключаются как в `ZKBio Attendance`.
- Тесты: `bench --site test.localhost run-tests --app access_registry` (rsync рабочего дерева в WSL-бенч, как в предыдущем плане).

## Review Focus

1. Порог «9:10» без ведущего нуля должен парситься как 09:10 → тест `test_threshold_filter` (Task 1, порог «9:10»).
2. Событие ровно в порог (09:00:00 при пороге 09:00) — не опоздание → тест `test_exact_threshold_is_not_late` (Task 1).
3. Пустые даты в Auto Email Report — умолчание «вчера» → тест `test_default_dates_yesterday` (Task 1).
4. Отсутствие «до дальнейшего» (date_to пуст, until_expected пуст) покрывает день через coalesce — тестом фикстуры Task 1 (отсутствие с открытым концом) → `test_absent_excluded` (Task 1).
5. Старый отчёт без новых фильтров ведёт себя как раньше (регрессия) → тест `test_attendance_defaults_unchanged` (Task 2).

---

### Task 1: Общий хелпер + отчёт `ZKBio Latecomers`

**Files:**
- Modify: `access_registry/zkbio/report/zkbio_attendance/zkbio_attendance.py` (вынос `attendance_rows`)
- Create: `access_registry/zkbio/report/zkbio_latecomers/{__init__.py,zkbio_latecomers.json,zkbio_latecomers.js,zkbio_latecomers.py}`
- Modify: `access_registry/access_registry/workspace/скуд/скуд.json` (ссылка Report, link_count 7)
- Test: `access_registry/tests/test_zkbio.py` (TestZKBioLatecomersReport)

**Interfaces:**
- Consumes: `ZKBio Event`, `HR Absence`, `department_map`, `column` (уже в zkbio_attendance).
- Produces:
  - `zkbio_attendance.attendance_rows(filters: dict, threshold: str | None = None) -> list[dict]` — строки «день × сотрудник» (day, person, person_name, department, first_event, last_event, events_count, points, absence, + late_minutes при threshold); уважает `show_absent`, `show_weekends`, `department`; даты не обязательны — умолчание вчера (`add_days(getdate(), -1)` для обеих).
  - `zkbio_latecomers.execute(filters) -> (columns, rows)`.

- [ ] **Step 1: failing tests**

Фикстура (хелперы `make_server`, `make_hr_person`, `person_row`, `event_row`, `fetch_with` уже в файле): сервер `TSKDLT`; понедельник `MON = "2026-11-23"`, суббота `SAT = "2026-11-28"`. Сотрудники: «Ранний» (события 08:55 и 09:00:00 в MON), «Поздний» (09:20 MON, событие и в SAT 10:00), «Отпускная» (09:30 MON + `HR Absence` с открытым концом: date_from=MON, date_to=None, until_expected=None).

```python
class TestZKBioLatecomersReport(FrappeTestCase):
	# setUpClass: persons sync [l1 Ранний pin=701, l2 Поздний pin=702, l3 Отпускная pin=703],
	# events sync, затем HR Absence (record_key "ZUP1:g-late:ОТ:2026-11-23") с пустыми датами конца

	def test_late_minutes_and_only_late_default(self):
		# execute({"date_from": MON, "date_to": MON}) → только Поздний, late_minutes == 20, first_event "09:20"
	def test_only_late_off_shows_all(self):
		# only_late=0 → Ранний и Поздний; у Раннего late_minutes falsy
	def test_threshold_filter(self):
		# threshold "9:10" → Поздний late_minutes == 10
	def test_exact_threshold_is_not_late(self):
		# threshold "09:00" → у Раннего (есть событие 09:00:00, но первое — 08:55) нет строки; и при
		# threshold "08:55" Ранний не опоздал (строго >)
	def test_weekends_hidden_by_default(self):
		# период MON..SAT: SAT-строка Позднего скрыта; show_weekends=1 → видна
	def test_absent_excluded(self):
		# Отпускная скрыта по умолчанию; show_absent=1 → видна с absence "Отпуск ежегодный"
	def test_default_dates_yesterday(self):
		# execute({}) возвращает те же (person, day), что execute({"date_from": вчера, "date_to": вчера})
```

- [ ] **Step 2: run — FAIL** (модуля zkbio_latecomers нет)

- [ ] **Step 3: реализация**

`zkbio_attendance.py`: SQL переносится в функцию `_query(filters, threshold=None) -> (sql, params)`; при `threshold` добавляется выражение `timestampdiff(minute, time(%(threshold)s), min(time(e.event_time))) as late_minutes` и `%(threshold)s` в params; при `not show_weekends` — `and weekday(date(e.event_time)) <= 4`; даты — `getdate(filters.get("date_from")) or add_days(getdate(), -1)`. `attendance_rows(filters, threshold=None)` — общий код фильтров show_absent/department и догрузки person_name/department (нынешнее тело execute). `execute()` zkbio_attendance переписывается на `attendance_rows` (колонки прежние; поведение с явными датами не меняется).

`zkbio_latecomers.py`:

```python
def execute(filters=None):
	filters = filters or {}
	threshold = filters.get("threshold") or "09:00"
	rows = attendance_rows(filters, threshold=threshold)
	if cint(filters.get("only_late", 1)):
		rows = [r for r in rows if r.late_minutes and r.late_minutes > 0]
	for row in rows:
		if row.late_minutes is not None and row.late_minutes <= 0:
			row.late_minutes = None  # пришёл вовремя или раньше
	return _columns(), rows
```

Колонки: day, person_name, person, department, first_event, late_minutes (Int, «Опоздание, мин»), last_event, points, absence. JSON/JS по образцу zkbio_attendance: фильтры date_from/date_to (Date, без reqd), threshold (Data, default «09:00»), department, only_late (Check, default 1), show_absent, show_weekends. Workspace — ссылка, link_count 7.

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): latecomers report with configurable threshold`

---

### Task 2: `ZKBio Attendance` — выходные и умолчание «вчера»

**Files:**
- Modify: `zkbio_attendance.py` (флаги уже в хелпере из Task 1; добавить колонки? нет — только фильтры) и `zkbio_attendance.js` (фильтры show_weekends; date_from/date_to больше не reqd, default убрать — умолчание вчера считает сервер)
- Test: `access_registry/tests/test_zkbio.py` (добавки в TestZKBioAttendanceReport)

- [ ] **Step 1: failing tests**

```python
	def test_attendance_defaults_unchanged(self):
		# явные даты сегодня → строка Петра на месте (регрессия)
	def test_attendance_weekends_hidden_by_default(self):
		# событие Петра в субботу SAT: в периоде day..SAT скрыто; show_weekends=1 → видно
```

(В setUpClass TestZKBioAttendanceReport добавить событие cls.present в `SAT 10:00`, pin 501.)

- [ ] **Step 2: run — FAIL** (суббота видна/умолчание не работает)

- [ ] **Step 3: реализация**

`zkbio_attendance.js`: убрать `reqd: 1` и default у дат (умолчание «вчера» на сервере), добавить `show_weekends` (Check, «Показывать выходные»). Питон уже готов из Task 1.

- [ ] **Step 4: run — PASS**

- [ ] **Step 5: Commit** `feat(zkbio): attendance report weekends flag and default dates`

---

### Task 3: README, полный прогон

**Files:**
- Modify: `README.md` (раздел «СКУД»: отчёт «опоздания», блок «Рассылка отчёта руководству» — Auto Email Report по шагам; примечание про имя отправителя латиницей из-за шлюза)
- Modify: `docs/architecture.md` (строка про отчёт опозданий)

- [ ] **Step 1:** `ruff check access_registry` + `ruff format --check` — чисто
- [ ] **Step 2:** полный прогон на чистом сайте: 463+N тестов OK
- [ ] **Step 3:** `git status` без мусора; Commit `docs(zkbio): latecomers report, auto-email setup, sender name workaround`
