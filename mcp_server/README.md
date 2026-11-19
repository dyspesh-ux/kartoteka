# MCP-сервер Картотеки

Даёт агентам (Claude Desktop, Kimi Code и любым MCP-клиентам) доступ к реестру доступов:
анализ учёток и прав, контрольные списки, отчёты и **предложение ролей доступа для
сотрудников**. Сервер — тонкий адаптер поверх REST API реестра и работает **только на
чтение**: никаких записей, назначений и гашений через MCP нет — решения принимает
человек в приложении.

## Как это устроено

```
агент (MCP-клиент) ──stdio──> mcp_server (python -m mcp_server.server)
                                    │  GET /api/method/access_registry....
                                    ▼
                              сайт Frappe (проверки прав — как обычно)
```

Сервер действует от имени выделенного пользователя Frappe. Все проверки прав
остаются в реестре: если пользователю сервера что-то не положено, API вернёт
PermissionError — так же, как браузеру.

## Установка

```bash
cd mcp_server
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

## Пользователь Frappe для сервера

1. Создайте пользователя (например `mcp@kartoteka`) **без** ролей System Manager
   и Registry Auditor.
2. Дайте роль **Access Catalog Viewer** — чтение сотрудников, каталогов прав и ролей,
   контрольных списков и отчётов. Персональные данные (даты рождения, отсутствия)
   эта роль не получает — API режет их сам.
3. User → Settings → API Access → Generate Keys. Ключ и секрет — только в env,
   в репозиторий не коммитить.

Если агенту нужны отчёты и списки по конкретным системам — права настраиваются как
обычно (профили доступа к системам, `app_access.py`), со стороны MCP ничего
дополнительно не требуется.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `KARTOTEKA_URL` | адрес сайта, например `https://registry.corp.example` |
| `KARTOTEKA_API_KEY` | API-ключ пользователя |
| `KARTOTEKA_API_SECRET` | секрет ключа |
| `KARTOTEKA_TIMEOUT` | таймаут запросов, сек (по умолчанию 30) |
| `KARTOTEKA_VERIFY_SSL` | `0` — не проверять TLS (только для отладки) |

## Подключение агента

**Kimi Code** — серверы MCP объявляются в `~/.kimi-code/mcp.json` (не в `config.toml`),
см. [документацию](https://moonshotai.github.io/kimi-code/en/customization/mcp.html):

```json
{
  "mcpServers": {
    "kartoteka": {
      "command": "C:/Users/<you>/Projects/kartoteka/mcp_server/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:/Users/<you>/Projects/kartoteka",
      "env": {
        "KARTOTEKA_URL": "https://registry.corp.example",
        "KARTOTEKA_API_KEY": "...",
        "KARTOTEKA_API_SECRET": "..."
      }
    }
  }
}
```

Интерактивно сервер добавляется командой `/mcp-config`, статус подключения показывает `/mcp`.
Новый сервер подхватывается при создании сессии: после правки `mcp.json` откройте новую
сессию (`/new`), уже открытые её не увидят.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "kartoteka": {
      "command": "C:/Users/<you>/.venv-kartoteka/Scripts/python.exe",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:/Users/<you>/Projects/kartoteka",
      "env": {
        "KARTOTEKA_URL": "https://registry.corp.example",
        "KARTOTEKA_API_KEY": "...",
        "KARTOTEKA_API_SECRET": "..."
      }
    }
  }
}
```

## Инструменты

| Инструмент | Что даёт |
|---|---|
| `whoami` | кто пользователь сервера и что видит (проверка связи) |
| `search_people` | поиск сотрудников, флаги рисков (`dismissed_access`, `no_access`, …) |
| `get_person` | карточка 360°: учётки 1С/AD/Битрикс24, папки, техника, роли, сверка «положено/есть», SoD |
| `get_position_profile` | должность: держатели, роли по правилам, типовой доступ |
| `list_access_roles` / `get_access_role` | каталог ролей / роль: правила, состав, члены с соответствием |
| `list_entitlements` / `get_entitlement` | каталог прав / право: держатели, роли и роли процессов, дающие его |
| `get_diadoc_organizations` / `get_diadoc_users` | организации Диадок: свежесть выгрузок / пользователи и их флаги прав |
| `list_processes` / `get_process` | бизнес-процессы и нужные им права |
| `list_reports` / `run_report` | отчёты: role mining, SoD, сверка, вне каталога и др. |
| `list_control_findings` | контрольные списки: `excess`, `missing`, `sod`, `privileged`, `stale`, … |
| `search_registry` | сквозной поиск |
| `suggest_roles` | предложение ролей для сотрудника (см. ниже) |

## Предложение ролей: `suggest_roles`

Один вызов собирает всё для решения «какие роли дать сотруднику»:

- текущие роли и причины членства;
- «Не хватает» по ролевой модели — с указанием, какая роль должна давать право
  (`expected_by`), и списком всех ролей, которые его дают (`granted_by`);
- типовой доступ должности, которого у сотрудника нет и который не требует модель
  (`kind=typical`, доля носителей должности);
- текущие SoD-конфликты и лишние доступы — чтобы предложение не усугубило их.

Агент рассуждает на этих данных и предлагает вариант; назначает роль человек
в приложении (или через уже существующие POST-методы API с проверками прав).

## Безопасность

- Только GET-методы API; в коде нет ни одного вызывающего запись вызова.
- Права — как у обычного пользователя реестра; административных ролей не давать.
- Секреты — только env; `.env` не коммитить.

Дизайн-решения: `docs/superpowers/specs/2026-11-13-mcp-server-design.md`.
