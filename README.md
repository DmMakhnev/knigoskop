# Книгоскоп

**Смотри в самую суть книги.**

Веб-приложение для поиска и импорта книг, чтения оригинала и многоуровневого AI-резюмирования.

## Восстановленная версия

Сохранён код фаз 1–6: аутентификация, библиотека и пять форматов импорта, разрешённые источники,
читалка, AI-инфраструктура, очередь и проверяемые версионированные резюме.
Это восстановленная dev-версия, а не подтверждение полной приёмки всех фаз.
Код фазы 7 (TTS, плеер и караоке) не найден. Прежние backend-тесты и браузерные скрипты отсутствуют.
Новые проверки восстановления и ограничения описаны в [отчёте](docs/04_RECOVERY_REPORT.md).
Публичный запуск заблокирован настройками до реализации производственной доставки email.

## Обязательная спецификация

- [Техническое задание](docs/01_TECHNICAL_REQUIREMENTS.md)
- [Архитектура](docs/02_SYSTEM_ARCHITECTURE.md)
- [План разработки](docs/03_CODEX_DEVELOPMENT_PLAN.md)

## Новый локальный запуск

Требуются Docker Compose v2, свободные порты 15173 и 18000. Команды предназначены для новой
локальной среды; при восстановлении не подключайте к ней действующие аварийные тома.
Имя проекта `knigoskop-restored` отделяет её от прежнего `knigoskop`.

```bash
cp .env.example .env
# Задайте собственный локальный POSTGRES_PASSWORD в .env до первого запуска.
docker compose --env-file .env -f infra/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose.yaml build api frontend worker migrate
docker compose --env-file .env -f infra/compose.yaml up -d postgres redis
docker compose --env-file .env -f infra/compose.yaml run --rm migrate
docker compose --env-file .env -f infra/compose.yaml up -d api frontend worker
```

В этой работе сборка образов, запуск нового стека и применение миграций не выполнялись.
Страница: http://localhost:15173; API: http://localhost:18000/health.
`/health` проверяет работу API, а не подключение к БД или очереди.
Cookie без Secure разрешены в примере только для локального HTTP; при HTTPS требуется `SESSION_COOKIE_SECURE=true`.
Worker запускает один Celery beat для durable outbox; не масштабируйте этот сервис с несколькими beat.
Файлы и письма dev-backend хранятся в отдельных Docker volumes. Письма содержат токены и не относятся к публичным артефактам.

Первый администратор и mock-маршруты создаются только в новой локальной БД:

```bash
docker compose --env-file .env -f infra/compose.yaml exec api python -m app.cli create-admin --email admin@example.com
docker compose --env-file .env -f infra/compose.yaml exec api python -m app.domains.summaries.cli seed-summary-mock
```

Пароль администратора вводится интерактивно. Mock не требует внешних ключей.
Настройки RouterAI и TTS не активируются этими командами; реальных запросов при восстановлении нет.

## Проверки исходников

Python 3.12+, Node 22+. Из корня репозитория:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.lock
pip install --no-deps -e 'backend[dev]'
python -m pytest backend/tests -q
ruff check backend
ruff format --check backend
```

```bash
cd frontend
npm ci
npm test
npm run build
npm run lint
```

Backend-проверки восстановления отключают настоящие ключи и не обращаются к действующим БД/Redis.
Это не замена утраченным интеграционным и сквозным тестам. Команды `test:*-browser` в восстановленном
package.json ссылаются на утраченные scripts и пока неработоспособны.

## Ручная резервная копия

[Порядок экспорта и сохранения](docs/05_MANUAL_BACKUP.md), [история](README-backup.md).
GitHub хранит исходники, доступные тесты, миграции, lock-файлы и документацию.
Ключи, .env, книги пользователей, дампы, журналы, образы и тома туда не отправляются.
