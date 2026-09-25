# Окружение разработчика: Windows + WSL2 + VS Code

Инструкция поднимает на своём компьютере то же, на чём разрабатывалось и тестировалось приложение:
Ubuntu 24.04 в WSL2, MariaDB 10.11, Redis, Python 3.11, Node 18, bench и Frappe v15, dev-сайт
в developer mode и VS Code, подключённый к WSL.

Время: около 30–40 минут, в основном ожидание загрузки.

## 1. WSL2 и Ubuntu (Windows)

В PowerShell от администратора:

```powershell
wsl --install -d Ubuntu-24.04
```

Перезагрузите компьютер, если попросит. При первом запуске Ubuntu задайте имя пользователя и пароль.

Выделите WSL достаточно памяти. Создайте файл `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=8GB
processors=4
```

и перезапустите WSL: `wsl --shutdown`.

Проверьте, что в Ubuntu включён systemd (в образах 24.04 он включён по умолчанию):

```bash
cat /etc/wsl.conf        # должно быть [boot] systemd=true
```

Если строки нет, добавьте её и снова выполните `wsl --shutdown` в PowerShell.

> **Работайте только в файловой системе Linux** (`~/…`), а не в `/mnt/c/…`. На диске Windows
> bench работает в разы медленнее, и ломаются права на файлы.

## 2. Системные пакеты (Ubuntu)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl build-essential pkg-config \
    python3-dev python3-venv pipx \
    mariadb-server mariadb-client libmariadb-dev \
    redis-server
```

Redis для bench запускается из `bench start` на своих портах (11000 и 13000), системная служба не
нужна:

```bash
sudo systemctl disable --now redis-server
```

## 3. MariaDB

Кодировка, которую требует Frappe:

```bash
sudo tee /etc/mysql/mariadb.conf.d/99-frappe.cnf > /dev/null <<'EOF'
[mysqld]
character-set-client-handshake = FALSE
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci

[mysql]
default-character-set = utf8mb4
EOF
sudo systemctl restart mariadb
```

Пароль root нужен bench для создания баз. Вход через `sudo mariadb` при этом сохраняется:

```bash
sudo mariadb -e "ALTER USER root@localhost IDENTIFIED VIA mysql_native_password USING PASSWORD('root') OR unix_socket; FLUSH PRIVILEGES;"
mariadb -uroot -proot -e "select version();"
```

Пароль `root` годится только для локальной разработки.

## 4. Python 3.11, Node 18, bench

**Python 3.11.** В Ubuntu 24.04 по умолчанию Python 3.12. Frappe v15 проверялся на 3.11, поэтому
ставим 3.11 через `uv`:

```bash
pipx ensurepath && source ~/.bashrc
pipx install uv
uv python install 3.11
```

**Node 18 и yarn** — через nvm:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 18
npm install -g yarn
```

**bench.** Самому `bench` нужны jinja2 и requests, поэтому их добавляем в его окружение:

```bash
pipx install frappe-bench
pipx inject frappe-bench jinja2 requests
bench --version
```

## 5. Bench с Frappe v15

```bash
cd ~
bench init frappe-bench --frappe-branch version-15 --python python3.11
cd ~/frappe-bench
```

`bench init` скачивает Frappe, ставит Python- и JS-зависимости и собирает интерфейс. Это
занимает 5–15 минут.

## 6. Приложение access_registry

Клонируем репозиторий прямо в `apps/access_registry` (папка должна называться как приложение):

```bash
cd ~/frappe-bench
git clone https://github.com/dyspesh-ux/kartoteka.git apps/access_registry
git -C apps/access_registry checkout claude/new-session-dkdpyd   # пока работа не влита в основную ветку
./env/bin/pip install -e apps/access_registry
# apps.txt может заканчиваться без перевода строки: сначала дописываем его, потом имя приложения
sed -i -e '$a\' sites/apps.txt && echo access_registry >> sites/apps.txt
cat sites/apps.txt   # должно быть две строки: frappe и access_registry
```

## 7. Dev-сайт

```bash
cd ~/frappe-bench
bench new-site dev.localhost --db-root-password root --admin-password admin
bench --site dev.localhost install-app access_registry
bench --site dev.localhost set-config developer_mode 1
bench --site dev.localhost set-config allow_tests true
bench --site dev.localhost enable-scheduler
bench use dev.localhost
bench build --app access_registry
```

Часовой пояс сайта от него зависят «сегодня» и расписание синка. Задаётся в интерфейсе
(System Settings → Time Zone) или командой:

```bash
bench --site dev.localhost execute frappe.client.set_value --args '["System Settings", "System Settings", "time_zone", "Europe/Moscow"]'
```

## 8. Запуск

```bash
cd ~/frappe-bench
bench start
```

`bench start` поднимает веб-сервер, Redis, воркеры очередей (в том числе `long`), планировщик и
сборщик интерфейса. Откройте в браузере Windows **http://localhost:8000**: WSL2 сам пробрасывает
порты. Вход: `Administrator` / `admin`.

Вся работа с ЗУП — в разделе **«Кадры ЗУП»**, первом пункте боковой панели
(http://localhost:8000/app/кадры-зуп): источники, журнал синхронизаций, люди, дерево
подразделений и всё, что требует разбора. Описание раздела — в README.

Остановить: `Ctrl+C` в терминале с `bench start`.

## 9. Проверка без ЗУП

В отдельном терминале поднимите заглушку HR_Export_API на синтетических данных:

```bash
cd ~/frappe-bench/apps/access_registry
python3 tools/mock_hr_export.py --dir access_registry/tests/fixtures/zup1 --port 8765 --password secret
# или большой набор:
# python3 tools/generate_synthetic.py --out ~/zup_big --employees 5000 --departments 400
# python3 tools/mock_hr_export.py --dir ~/zup_big --port 8765 --password secret
```

В разделе **«Кадры ЗУП» → «Источники ЗУП»** создайте источник: код `ZUP1`, базовый URL
`http://127.0.0.1:8765/hs/hr_export`, пользователь `svc_hr_export`, пароль `secret`. Нажмите
**«Синхронизировать сейчас»**.

Как понять, что синхронизация прошла:

1. «Кадры ЗУП» → **«Журнал синхронизаций»**: у последнего запуска статус **«Успех»**, при первом
   запуске отмечена «Первая загрузка». В поле «Счётчики» — сколько записей создано.
2. В форме источника заполнены «Последний успешный синк» и «Статус последнего запуска».
3. На ярлыках «Люди» и «Трудоустройства» появились счётчики, в «Дереве подразделений» видны
   организации и подразделения.
4. Статус «В процессе» дольше нескольких минут значит, что не работает воркер очереди `long`: он
   должен быть в выводе `bench start`.

Реальную базу ЗУП подключают так же: указывают URL её HTTP-сервиса HR_Export_API и пароль
пользователя `svc_hr_export`. Что проверить после первой загрузки на реальных данных — в README,
раздел «Проверка на реальной базе».

## 10. Тесты и линтер

```bash
cd ~/frappe-bench
bench --site dev.localhost run-tests --app access_registry
# один модуль или тест:
bench --site dev.localhost run-tests --module access_registry.tests.test_zup_sync --test test_03_transfer

pipx install ruff
cd apps/access_registry && ruff check . && ruff format --check .
```

Тесты перед каждым прогоном очищают таблицы модуля (в транзакции, которая откатывается). Всё равно
запускайте их только на dev-сайте.

## 11. VS Code

1. Поставьте VS Code **в Windows** и расширение **WSL** (`ms-vscode-remote.remote-wsl`).
2. Откройте папку bench из терминала Ubuntu:

   ```bash
   cd ~/frappe-bench && code .
   ```

   VS Code откроется в режиме WSL: в левом нижнем углу будет «WSL: Ubuntu-24.04».
3. Поставьте расширения **внутри WSL** (кнопка «Install in WSL»): Python (`ms-python.python`),
   Pylance, Ruff (`charliermarsh.ruff`).
4. Выберите интерпретатор `~/frappe-bench/env/bin/python` (`Ctrl+Shift+P` → *Python: Select
   Interpreter*).
5. Создайте `~/frappe-bench/.vscode/settings.json`:

   ```json
   {
     "python.defaultInterpreterPath": "${workspaceFolder}/env/bin/python",
     "python.analysis.extraPaths": ["${workspaceFolder}/apps/frappe", "${workspaceFolder}/apps/access_registry"],
     "[python]": {
       "editor.defaultFormatter": "charliermarsh.ruff",
       "editor.insertSpaces": false,
       "editor.tabSize": 4
     },
     "files.eol": "\n",
     "files.watcherExclude": {
       "**/node_modules/**": true,
       "**/env/**": true,
       "**/sites/assets/**": true
     }
   }
   ```

   Frappe и это приложение используют табы для отступов.

### Отладка

Чтобы ставить точки останова в синке и в тестах, создайте `~/frappe-bench/.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Tests: access_registry",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/apps/frappe/frappe/utils/bench_helper.py",
      "args": ["frappe", "--site", "dev.localhost", "run-tests", "--module", "access_registry.tests.test_zup_sync"],
      "cwd": "${workspaceFolder}/sites",
      "python": "${workspaceFolder}/env/bin/python",
      "env": { "DEV_SERVER": "1" },
      "justMyCode": false
    },
    {
      "name": "Web server (bench serve)",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/apps/frappe/frappe/utils/bench_helper.py",
      "args": ["frappe", "serve", "--port", "8000", "--noreload", "--nothreading"],
      "cwd": "${workspaceFolder}/sites",
      "python": "${workspaceFolder}/env/bin/python",
      "env": { "DEV_SERVER": "1" }
    },
    {
      "name": "Worker (очередь long)",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/apps/frappe/frappe/utils/bench_helper.py",
      "args": ["frappe", "worker", "--queue", "long"],
      "cwd": "${workspaceFolder}/sites",
      "python": "${workspaceFolder}/env/bin/python"
    }
  ]
}
```

Чтобы отлаживать веб-сервер или воркер, уберите соответствующую строку (`web:` или
`worker:`) из `~/frappe-bench/Procfile` и запустите `bench start`. Остальные процессы
поднимет bench, а отлаживаемый — VS Code.

## 12. Обновление приложения

Когда в репозитории появились изменения:

`bench start` должен работать в другом терминале: `migrate` без запущенного Redis падает с
ошибкой «Service redis_cache is not running».

```bash
cd ~/frappe-bench/apps/access_registry
git pull
cd ~/frappe-bench
bench --site dev.localhost migrate
bench build --app access_registry
bench --site dev.localhost clear-cache
```

`migrate` применяет изменения DocType, раздела «Кадры ЗУП» и патчи. Затем обновите страницу в
браузере (Ctrl+Shift+R).

## 13. Git

Работайте с git внутри WSL, чтобы не было проблем с концами строк:

```bash
git config --global user.name "Имя Фамилия"
git config --global user.email "you@example.com"
git config --global core.autocrlf input
```

Правки DocType, сделанные в интерфейсе в developer mode, сохраняются в JSON-файлы
`apps/access_registry/access_registry/access_registry/doctype/…`. Их нужно коммитить. После
`git pull` выполните `bench --site dev.localhost migrate`.

## Если что-то не работает

| Симптом | Что сделать |
|---|---|
| `bench init` падает на `uv venv … python3.11` | `uv python install 3.11`, затем снова `bench init` (перед этим удалите папку `frappe-bench`) |
| `No module named 'frappeaccess_registry'` | в `sites/apps.txt` имена слиплись в одну строку: `printf 'frappe\naccess_registry\n' > sites/apps.txt` |
| `Service redis_cache is not running` при `migrate` | в другом терминале запустите `bench start` и повторите |
| Раздела «Кадры ЗУП» нет в боковой панели | `bench start` запущен, затем `bench --site dev.localhost migrate` и `clear-cache` |
| `ModuleNotFoundError: jinja2` при запуске bench | `pipx inject frappe-bench jinja2 requests` |
| `Access denied for user 'root'` при `new-site` | повторите `ALTER USER` из шага 3 |
| MariaDB не стартует после перезагрузки | `sudo systemctl status mariadb`, проверьте, что systemd включён (шаг 1) |
| Порт 8000 или 11000/13000 занят | остался старый `bench start`: `pkill -f "bench start"`; `sudo systemctl disable --now redis-server` |
| http://localhost:8000 не открывается из Windows | в Windows: `wsl --shutdown`, снова откройте Ubuntu и выполните `bench start` |
| В интерфейсе нет стилей | `bench build` и перезапустите `bench start` |
| Синк стоит «В процессе» | не запущен воркер `long`: он должен быть в выводе `bench start` |
