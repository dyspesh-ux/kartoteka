# Сверка должности/отдела ЗУП ↔ AD в списке «Расхождения данных» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В списке «Контроль» → «Расхождения данных» (`kind=quality`) появляются строки
`AD: должность` и `AD: отдел` для сотрудников, у которых атрибуты `title`/`department`
учётки AD не совпадают с должностью/отделом основного трудоустройства из ЗУП.

**Architecture:** Только серверная выборка в `_control_quality()` (`access_registry/registry/api.py`):
join включённых учёток AD с работающими сотрудниками, эталон — `main_places()` (основное
активное трудоустройство), сравнение через `normalize_name()`. Новых DocType, хранения,
права, фронтенда-списков и видов гашения не добавляется: список, гашение, журнал, MCP и
дашборд работают как есть.

**Tech Stack:** Frappe v15, Python 3.10+, тесты — `FrappeTestCase` под bench.

**Spec:** `docs/superpowers/specs/2026-11-17-zup-ad-titles-control-design.md`

## Global Constraints

- Python: табы для отступов, двойные кавычки, длина строки 110 (`ruff`, `pyproject.toml`).
- Код и комментарии — на английском; строки интерфейса (значения `area`, `current` «—») — на русском.
- Каждый метод API сам проверяет права — изменения в `control()` не требуются (existing `aa.require_control`).
- Тесты запускаются только под bench (MariaDB + Frappe); локально на этой машине недоступны —
  локальная проверка: `ruff check` + `ruff format --check`; полный прогон — CI (`.github/workflows/ci.yml`).
- Новых scheduled-задач и DocType не добавляем.
- Тестовые данные: фикстура `RegistryFixture` — работающие с учётками AD: Иванов (person 1,
  должность «Генеральный директор», отдел «Дирекция»), Петрова (2, «Главный бухгалтер»,
  «Бухгалтерия»), Сидоров (3, «Менеджер по продажам», «Отдел продаж»); Фёдоров (8) и
  Новиков (7) уволены с включёнными учётками; у учёток фикстуры AD `title`/`department` пустые.

## Review Focus

- Сотрудник с несколькими активными трудоустройствами: эталон — основное место (`main_places`).
  В фикстуре такого человека с учёткой AD нет (Кузнецова — без AD) — не покрыто тестом,
  поведение опирается на `main_places`, который покрыт в `test_ad_plan.py`.
- Пустой эталон (нет активного трудоустройства / пустые `position_title`): поле пропускается,
  строка не пишется — в фикстуре не встречается, не покрыто.
- У пользователя без доступа к системе AD строки не видны (`aa.filter_rows` по `system`) —
  существующая механика, тестом не закреплено.
- Нормализация латинских «похожих» букв («диpeктор» латиницей = «директор») — поведение
  `normalize_name`, в тесте закреплён базовый случай (регистр/пробелы).
- Гашение новых строк: ключ quality `(area, person, full_name)` различает должность и отдел
  одного человека — закреплено тестом в Task 1.

---

### Task 1: Строки «AD: должность» и «AD: отдел» в `_control_quality`

**Files:**
- Modify: `access_registry/registry/api.py` (функция `_control_quality`, ~line 1558–1607; добавить импорт `normalize_name`)
- Test: `access_registry/tests/test_registry_control.py`

**Interfaces:**
- Consumes: `main_places` (уже импортирован в `api.py` из `access_registry.access_catalog.access_report`); `normalize_name` из `access_registry.sync.normalize` (добавить импорт); `frappe.db.sql`.
- Produces: новые строки в `api.control("quality")["rows"]` с ключами
  `{"person", "full_name", "area": "AD: должность" | "AD: отдел", "system": "AD", "current", "expected", "ref", "ref_doctype": "AD Account"}`.
  `current` = значение из AD или `"—"` при пустом; `expected` = `position_title`/`department_title`
  из `main_places`. Колонки списка не меняются.

- [ ] **Step 1: Написать падающие тесты**

В `access_registry/tests/test_registry_control.py` добавить класс после `TestControlLists`:

```python
class TestQualityTitlesDepartments(RegistryFixture):
	"""«AD: должность» и «AD: отдел»: эталон — ЗУП, факт — атрибуты учётки AD."""

	def rows(self):
		return [
			r
			for r in api.control("quality")["rows"]
			if r["area"] in ("AD: должность", "AD: отдел")
		]

	def ad_account(self, n):
		return frappe.db.get_value("AD Account", {"person": self.person(n), "enabled": 1}, "name")

	def test_empty_ad_attributes_are_discrepancies(self):
		rows = self.rows()
		names = {r["full_name"] for r in rows}
		# работают и имеют учётку AD; Фёдоров и Новиков уволены — вне списка
		self.assertEqual(names, {"Иванов Иван Иванович", "Петрова Мария Сергеевна", "Сидоров Пётр Алексеевич"})
		ivanov = next(r for r in rows if r["person"] == self.person(1) and r["area"] == "AD: должность")
		self.assertEqual((ivanov["current"], ivanov["expected"]), ("—", "Генеральный директор"))
		self.assertTrue(ivanov["ref"].startswith("TSTAD:") and ivanov["ref_doctype"] == "AD Account")

	def test_match_after_normalization_disappears(self):
		frappe.db.set_value("AD Account", self.ad_account(1), "title", "  ГЕНЕРАЛЬНЫЙ   директор ")
		rows = self.rows()
		self.assertFalse([r for r in rows if r["person"] == self.person(1) and r["area"] == "AD: должность"])
		# отдел в AD по-прежнему пуст — строка по отделу осталась
		self.assertTrue([r for r in rows if r["person"] == self.person(1) and r["area"] == "AD: отдел"])

	def test_different_department_is_a_discrepancy(self):
		frappe.db.set_value("AD Account", self.ad_account(2), "department", "Продажи")
		dept = next(r for r in self.rows() if r["person"] == self.person(2) and r["area"] == "AD: отдел")
		self.assertEqual((dept["current"], dept["expected"]), ("Продажи", "Бухгалтерия"))

	def test_disabled_account_is_excluded(self):
		frappe.db.set_value("AD Account", self.ad_account(1), "enabled", 0)
		self.assertFalse([r for r in self.rows() if r["person"] == self.person(1)])

	def test_suppression_key_separates_areas(self):
		ivanov = next(r for r in self.rows() if r["person"] == self.person(1) and r["area"] == "AD: должность")
		api.suppress_alerts("quality", [ivanov["alert_key"]], "скоро поправят в AD")
		rows = self.rows()
		self.assertFalse([r for r in rows if r["person"] == self.person(1) and r["area"] == "AD: должность"])
		self.assertTrue([r for r in rows if r["person"] == self.person(1) and r["area"] == "AD: отдел"])
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `bench --site <site> run-tests --app access_registry --module access_registry.tests.test_registry_control`
Expected: новые тесты падают — строк «AD: должность»/«AD: отдел» нет (AssertionError на пустом списке).
(Локально bench недоступен — прогон в CI/на стенде; локально достаточно `ruff check access_registry/tests/test_registry_control.py`.)

- [ ] **Step 3: Реализовать в `_control_quality()` (`access_registry/registry/api.py`)**

1. Добавить в импорты: `from access_registry.sync.normalize import normalize_name`.
2. В конец `_control_quality()` (перед `return`) добавить блок:

```python
	ad_accounts = frappe.db.sql(
		"""select a.name as ref, a.person, a.title, a.department, p.full_name
		from `tabAD Account` a join `tabPerson` p on p.name = a.person
		where a.enabled = 1 and a.missing_in_source = 0 and p.status = 'Работает'""",
		as_dict=True,
	)
	places = main_places([r.person for r in ad_accounts])
	for r in ad_accounts:
		place = places.get(r.person) or {}
		for area, current, expected in (
			("AD: должность", r.title, place.get("position_title")),
			("AD: отдел", r.department, place.get("department_title")),
		):
			if not expected or normalize_name(current) == normalize_name(expected):
				continue
			rows.append(
				{
					"person": r.person,
					"full_name": r.full_name,
					"area": area,
					"system": "AD",
					"current": current or "—",
					"expected": expected,
					"ref": r.ref,
					"ref_doctype": "AD Account",
				}
			)
```

- [ ] **Step 4: Прогнать тесты**

Run: `bench --site <site> run-tests --app access_registry --module access_registry.tests.test_registry_control`
Expected: PASS (все тесты модуля, включая существующие — новые строки не ломают
`test_structure_of_every_list` и `test_quality`, они фильтруют по `area`).

- [ ] **Step 5: Commit**

```bash
git add access_registry/registry/api.py access_registry/tests/test_registry_control.py
git commit -m "feat(control): ZUP vs AD position and department discrepancies in quality list"
```

### Task 2: Подсказка на дашборде, README, линтер

**Files:**
- Modify: `access_registry/www/registry.js` (KPI-hint «Расхождения с кадрами», ~line 672)
- Modify: `README.md` (места, перечисляющие состав quality)

**Interfaces:**
- Consumes: строки `area` из Task 1 («AD: должность», «AD: отдел»).
- Produces: нет (только тексты).

- [ ] **Step 1: Обновить hint KPI на дашборде**

В `access_registry/www/registry.js` (~line 672) заменить:
`hint: "профили Битрикс24, руководители, employeeNumber в AD"` →
`hint: "профили и руководители Битрикс24, employeeNumber, должность и отдел в AD"`.

- [ ] **Step 2: Обновить README**

Найти места, описывающие состав «Расхождений данных» (`grep -n "employeeNumber" README.md`,
строки ~204–212 и раздел про контрольные списки): добавить должность и отдел AD в
перечень того, что сверяется с кадрами. Не менять остальное.

- [ ] **Step 3: Линтер и форматирование**

Run: `ruff check access_registry && ruff format --check access_registry`
Expected: без замечаний (исправить при необходимости: `ruff check --fix`, `ruff format`).

- [ ] **Step 4: Полный прогон тестов и commit**

Run: `bench --site <site> run-tests --app access_registry`
Expected: PASS. Затем:

```bash
git add access_registry/www/registry.js README.md
git commit -m "docs(control): mention ZUP vs AD titles and departments in quality hints"
```
