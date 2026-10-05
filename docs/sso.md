# Вход через SSO

Окно входа реестра (`/login`) показывает только кнопки корпоративного входа (SSO) — пароль от
реестра пользователям не нужен. Поддерживается любой провайдер OAuth 2.0 / OpenID Connect:
Microsoft Entra ID (Microsoft 365), Keycloak, а через Keycloak — и ADFS или Active Directory.

Как ведёт себя страница:

| Ситуация | Что видно на `/login` |
|---|---|
| SSO ещё не настроен (первая установка) | форма входа по паролю — чтобы администратор вошёл и настроил SSO |
| SSO настроен | только кнопка «Войти через …» |
| SSO настроен, адрес `/login?local=1` | кнопка SSO и аварийный вход администратора по паролю |
| вход по паролю отключён полностью (шаг 4) | только кнопка SSO, пароль не принимает и сервер |

## 1. Пользователи

SSO только подтверждает, кто входит: пользователь реестра с той же почтой должен уже быть
(админка → «Пользователи»), а доступ к разделам ему дают роли или профиль доступа
([README](../README.md#доступ-к-приложению-по-разделам)). В настройке провайдера ставьте
«Регистрация» (Sign ups) = **Deny**: тогда войти сможет только заведённый пользователь.

## 2. Провайдер

Админка → «Ключ социального входа» (`Social Login Key`) → «Добавить». Адрес возврата
(Redirect URI), который нужно указать на стороне провайдера, — в поле «Redirect URL» с адресом
сайта впереди: `https://<сайт реестра><Redirect URL>`.

### Microsoft Entra ID (Microsoft 365)

1. Entra ID → «Регистрация приложений» → «Новая регистрация»: имя «Реестр доступа», учётные записи
   только своей организации, Redirect URI (Web):
   `https://<сайт>/api/method/frappe.integrations.oauth2_logins.login_via_office365`.
2. «Сертификаты и секреты» → новый секрет клиента.
3. В реестре: провайдер **Office 365**, Client ID — «Идентификатор приложения (клиента)», Client
   Secret — секрет. В «Authorize URL» и «Access Token URL» замените `common` на идентификатор
   своего клиента (tenant ID), чтобы вход был только для своей организации.

### Keycloak (и ADFS / AD через него)

1. Keycloak → realm → Clients → Create: OpenID Connect, Client ID `registry`, Client
   authentication = On, Valid redirect URIs:
   `https://<сайт>/api/method/frappe.integrations.oauth2_logins.custom/keycloak`.
2. В реестре: провайдер **Custom**, «Provider Name» — `Keycloak` (это слово будет на кнопке:
   «Войти через Keycloak»; можно написать «корпоративную учётную запись»), Client ID и секрет,
   - Base URL `https://<keycloak>/realms/<realm>`
   - Authorize URL `/protocol/openid-connect/auth`
   - Access Token URL `/protocol/openid-connect/token`
   - API Endpoint `/protocol/openid-connect/userinfo`
   - Redirect URL `/api/method/frappe.integrations.oauth2_logins.custom/keycloak`
   - Auth URL Data `{"response_type": "code", "scope": "openid email profile"}`
   - User ID Property `sub`
3. Пользователи AD попадают в Keycloak через User Federation (LDAP) или через ADFS как внешний
   провайдер (Identity Providers → OpenID Connect / SAML).

## 3. Проверка

Откройте `/login` в окне инкогнито: должна быть только кнопка «Войти через …». Войдите под
обычным пользователем, затем под администратором. Если кнопки нет — у провайдера не заполнены
Client ID, секрет или Base URL, либо не стоит «Включите социальный вход».

## 4. Запретить вход по паролю совсем

Когда SSO проверен: админка → «Системные настройки» → «Вход» → **«Отключить вход по имени
пользователя/паролю»**. После этого пароль не принимает и сервер (в том числе аварийная форма `/login?local=1`).
Не затрагивает: API-ключи сборщика Synology и n8n, загрузки по расписанию.

Если SSO сломался и войти нельзя, вход по паролю возвращается с сервера:

```bash
bench --site <сайт> console          # в Docker: docker compose … exec backend bench --site <сайт> console
>>> frappe.db.set_single_value("System Settings", "disable_user_pass_login", 0); frappe.db.commit()
```

Затем `/login?local=1` и вход администратора.
