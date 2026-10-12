# Книгоскоп

**Смотри в самую суть книги.**

Веб-приложение для поиска и импорта книг, чтения оригинала и многоуровневого AI-резюмирования.

## Восстановленная версия

Сохранён код фаз 1–6: аутентификация, библиотека и пять форматов импорта, разрешённые источники,
читалка, AI-инфраструктура, очередь и проверяемые версионированные резюме.
Это восстановленная dev-версия, а не подтверждение полной приёмки всех фаз.
Прежний код фазы 7 не найден; новая реализация TTS и плеера описана в [отчёте фазы 7](docs/07_TTS_VALIDATION.md). Прежние backend-тесты и браузерные скрипты отсутствуют.
Новые проверки восстановления и ограничения описаны в [отчёте](docs/04_RECOVERY_REPORT.md).
Основной сценарий проверен в [отдельном Docker-стеке](docs/06_RECOVERY_VALIDATION.md): 14 исходных интеграционных сценариев прошли; с фазой 7 их стало 17.
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

При восстановлении образы не пересобирались. Миграции и приложение проверены только на отдельном временном стеке; действующая БД не менялась.
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
Это не замена всем утраченным интеграционным и сквозным тестам. Старые команды `test:*-browser` в восстановленном
package.json ссылаются на утраченные scripts и пока неработоспособны.

## Проверка в изолированном Docker-стеке

Для среды восстановления с уже сохранёнными образами:

```bash
python scripts/validate_recovery.py
```

Требуются локальный Docker daemon и существующие `knigoskop-api:latest`,
`knigoskop-frontend:latest`, `postgres:17-alpine`, `redis:7-alpine`.
Скрипт не собирает и не скачивает образы. Для других локальных образов предусмотрены
`--api-image` и `--frontend-image`.
Создаются отдельная внутренняя сеть и временная БД `knigoskop_validation`.
Исходники подключаются только для чтения, внешние ключи не передаются, все AI-маршруты — mock.
Тестовые контейнеры удаляются после проверки; исходные контейнеры и тома остаются на месте.
Синтетические файлы, письма и журналы проверки остаются в игнорируемой `.cache/validation/`.
Интеграционные проверки запускаются отдельно от `backend/tests`; обычный CI их не выполняет.

## Ручная резервная копия

[Порядок экспорта и сохранения](docs/05_MANUAL_BACKUP.md), [история](README-backup.md).
GitHub хранит исходники, доступные тесты, миграции, lock-файлы и документацию.
Ключи, .env, книги пользователей, дампы, журналы, образы и тома туда не отправляются.

## Озвучивание (фаза 7)

GenVoice — основной серверный адаптер, Yandex SpeechKit v1 — выбираемая альтернатива,
Web Speech API — браузерный fallback с явным согласием. Реальные провайдеры ещё не проверены.
Плеер сохраняет прогресс и голос, использует приватный кэш и скорость 0,75–2×.
Если точных меток нет, интерфейс прямо сообщает об этом.

В новой локальной БД после миграций можно создать тестовый профиль:

```bash
docker compose --env-file .env -f infra/compose.yaml exec api python -m app.domains.audio.cli seed-audio-mock
```

Он генерирует тестовые сигналы, а не речь. Независимая браузерная проверка плеера:

```bash
node scripts/audio-browser-smoke.cjs
```

Требуются установленные frontend-зависимости и Chromium в `/usr/bin/chromium`.
Подключение ключей, получение каталога голосов и ограничения проверки описаны
в [протоколе TTS](docs/07_TTS_VALIDATION.md).
