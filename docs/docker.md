# Развёртывание в Docker (frappe_docker)

Реестр ставится по рекомендациям [frappe_docker](https://github.com/frappe/frappe_docker): свой
образ собирается из `images/layered/Containerfile` с приложением из `apps.json`, стек — из
`compose.yaml` и готовых оверрайдов frappe_docker. Всё нужное лежит в [`deploy/docker/`](../deploy/docker).

```
 сервер приложения (Docker)                            сервер базы данных
┌──────────────────────────────────────────┐          ┌───────────────────────┐
│ frontend (nginx) ← tls (свой сертификат)  │          │ MariaDB 10.11         │
│ backend (gunicorn)   websocket            │  3306    │ (Docker: deploy/docker│
│ queue-short  queue-long  scheduler        │ ───────► │  /db/compose.yaml     │
│ redis-cache  redis-queue  cron (бэкапы)   │          │  или пакет mariadb)   │
│ том sites: site_config, файлы, бэкапы    │          └───────────────────────┘
└──────────────────────────────────────────┘
```

**База — только MariaDB** (10.6–10.11). MySQL не подходит: Frappe использует последовательности
и `ADD INDEX IF NOT EXISTS`, которых в MySQL нет. PostgreSQL приложение не поддерживает.

## 1. Сервер базы данных

Вариант А — MariaDB в Docker (удобно, если на сервере уже есть MySQL: другой порт, пакеты не
конфликтуют):

```bash
cd deploy/docker/db
cp db.env.example db.env        # пароль root, порт (3307, если 3306 занят MySQL), память
docker compose --env-file db.env up -d
```

Вариант Б — пакет: `apt install mariadb-server` (Ubuntu 24.04 — MariaDB 10.11), в
`/etc/mysql/mariadb.conf.d/50-server.cnf`:

```ini
[mysqld]
bind-address = <адрес сервера базы>
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
skip-character-set-client-handshake
innodb_buffer_pool_size = 2G
```

В обоих вариантах:

- **Файрвол**: порт MariaDB открыт только для сервера приложения.
- **Root с сервера приложения** нужен один раз — чтобы `bench new-site` создал базу и
  пользователя сайта:
  ```sql
  CREATE USER 'root'@'<IP сервера приложения>' IDENTIFIED BY '<пароль>';
  GRANT ALL PRIVILEGES ON *.* TO 'root'@'<IP сервера приложения>' WITH GRANT OPTION;
  ```
  (в варианте А root уже доступен по сети — ограничьте доступ файрволом). После создания сайта
  этот доступ можно удалить: сайт работает под своим пользователем.

## 2. Сервер приложения: сборка образа

Нужны Docker Engine 23+ с compose v2 и git. Стек называется `registry`: тома — `registry_sites`,
`registry_redis-queue-data`.

```bash
git clone https://github.com/<owner>/kartoteka && cd kartoteka/deploy/docker
cp apps.json.example apps.json
./build.sh 2026-10-01          # образ access-registry:2026-10-01
```

- `apps.json` — откуда брать приложение (ветка `main`). Для закрытого репозитория — токен в URL:
  `https://<token>@github.com/<owner>/kartoteka`. Файл передаётся в сборку как секрет BuildKit и
  в образ не попадает; в git он не коммитится (`.gitignore`).
- Frappe — ветка `version-15`; коммит frappe_docker закреплён в `build.sh`
  (`FRAPPE_DOCKER_REF`), обновляйте его осознанно.
- За корпоративным прокси:
  `DOCKER_BUILD_ARGS="--network=host --build-arg HTTPS_PROXY=http://proxy:3128" ./build.sh`.
- Собрать можно и на другой машине (CI), затем `docker save | ssh … docker load` или через свой
  registry (`CUSTOM_IMAGE=registry.company.local/access-registry`, `PULL_POLICY=always`).

## 3. Настройка и запуск стека

```bash
cp registry.env.example registry.env     # DB_HOST, DB_PORT, CUSTOM_TAG, имя сайта, порты
./compose.sh own-cert                    # или http, или letsencrypt
docker compose -p registry -f compose.registry.generated.yaml up -d
```

Режимы HTTPS:

| Режим | Когда | Что нужно |
|---|---|---|
| `own-cert` | внутренняя сеть, корпоративный сертификат | `tls/registry.crt` (с цепочкой) и `tls/registry.key` |
| `http` | перед стеком уже стоит корпоративный обратный прокси с TLS | прокси ведёт на `HTTP_PUBLISH_PORT` (8080) |
| `letsencrypt` | имя в публичном DNS, порты 80/443 открыты из интернета | `SITES_RULE`, `LETSENCRYPT_EMAIL` |

`compose.sh` собирает итоговый файл из `compose.yaml` frappe_docker и оверрайдов: Redis
(`compose.redis.yaml`), бэкапы (`compose.backup-cron.yaml`) и выбранного режима; MariaDB-оверрайд
не используется — база внешняя. Воркер `queue-long` обязателен: в нём идут загрузки ЗУП, прав 1С,
AD и Битрикс24.

## 4. Сайт

```bash
S=registry.company.local         # то же, что FRAPPE_SITE_NAME_HEADER
dc() { docker compose -p registry -f compose.registry.generated.yaml "$@"; }

dc exec backend bench new-site $S \
    --mariadb-user-host-login-scope='<IP сервера приложения>' \
    --db-root-password '<пароль root MariaDB>' \
    --admin-password '<пароль Administrator>' \
    --install-app access_registry
dc exec backend bench --site $S enable-scheduler
dc exec backend bench --site $S set-config host_name "https://$S"   # ссылки в утренней сводке
```

`--mariadb-user-host-login-scope` — с какого адреса пользователь сайта входит в MariaDB: для
внешней базы это адрес сервера приложения (контейнеры выходят в сеть через него). Если MariaDB
в Docker на том же хосте, что и стек (тестовый стенд), подойдёт `'%'`. Затем — часовой
пояс (System Settings) и почта (Email Account), как в [deployment.md](deployment.md#2-сайт).

**Сохраните `encryption_key`** из `site_config.json` отдельно от бэкапов:
`dc exec backend cat sites/$S/site_config.json`. Без него пароли источников не расшифровать.

## 5. Обновление

```bash
cd kartoteka && git pull            # свежие deploy/docker и инструкции
cd deploy/docker && ./build.sh 2026-11-15
sed -i 's/^CUSTOM_TAG=.*/CUSTOM_TAG=2026-11-15/' registry.env
./compose.sh own-cert
dc exec backend bench --site all backup
docker compose -p registry -f compose.registry.generated.yaml up -d
dc exec backend bench --site all migrate
```

Обновление всегда через новый образ: в нём собраны статические файлы приложения (например,
`assets/access_registry/js/registry_desk.js` — полноширинный desk). `git pull` внутри контейнера
не подходит: контейнер пересоздаётся из образа.

Откат — вернуть прежний `CUSTOM_TAG` и `up -d` (если `migrate` уже изменил базу — восстановить
бэкап, сделанный перед обновлением).

## 6. Бэкапы

- **В стеке**: `cron` (оверрайд `compose.backup-cron.yaml`) каждые `BACKUP_CRONSTRING` (6 часов)
  делает `bench --site all backup` в том `sites` (`sites/<site>/private/backups`).
- **С сервера — обязательно**: копируйте их наружу, например cron на хосте:
  ```bash
  docker run --rm -v registry_sites:/sites:ro -v /backup/registry:/out alpine \
      sh -c 'cp -u /sites/*/private/backups/* /out/'
  ```
- **На сервере базы** — свои дампы (`mariadb-dump --single-transaction`) или снимки тома.
- Восстановление: `dc exec backend bench --site $S restore <файл.sql.gz>` + тот же `encryption_key`.

## 7. Что проверить после запуска

- `https://<сайт>/registry` открывается, вход под Administrator.
- `dc ps` — все сервисы `running`, `configurator` — `exited (0)`.
- `dc exec backend bench --site $S doctor` — планировщик включён, воркеры видны.
- Источники: из контейнеров должны быть доступны HTTP-сервисы 1С, контроллеры домена (LDAPS 636),
  Битрикс24; NAS и n8n присылают данные на `https://<сайт>/api/method/...`.
- Логи: `dc logs -f backend queue-long scheduler`.
