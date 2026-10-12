# Книгоскоп
**Смотри в самую суть книги.**

Многопользовательское веб-приложение для поиска и импорта книг, чтения оригинала,
многоуровневого AI-резюмирования и озвучивания с синхронной подсветкой.

## Текущее состояние
Минимальный каркас: React + TypeScript + Vite, FastAPI, стартовая страница,
`/health`, `/api/v1/status`, Celery worker и локальная инфраструктура PostgreSQL/Redis.
Запуск не требует внешних API-ключей. AI/TTS отключены явно.
Авторизация, библиотека, импорт, модели БД, миграции и обработка книг ещё не реализованы.
Это dev-окружение: публичное развёртывание и защищённая админка относятся к следующим фазам.
Healthcheck проверяет только работу API, не готовность БД и очереди.

## Документы
- [Техническое задание](docs/01_TECHNICAL_REQUIREMENTS.md)
- [Архитектура](docs/02_SYSTEM_ARCHITECTURE.md)
- [План разработки](docs/03_CODEX_DEVELOPMENT_PLAN.md)

## Структура
- `backend/app/`: API, конфигурация, домены, интеграции, worker.
- `backend/tests/`: проверки API.
- `frontend/src/`: приложение, страницы, компоненты, API и стили.
- `infra/`: Docker Compose для локального запуска.
- `docs/`: согласованные требования.
- `scripts/`: место для будущих служебных команд.

## Запуск через Docker Compose
Требуются Docker и Compose v2. Из корня репозитория:
```bash
cp .env.example .env
docker compose --env-file .env -f infra/compose.yaml config
docker compose --env-file .env -f infra/compose.yaml up --build
```
Страница: http://localhost:5173 · API: http://localhost:8000/health · Swagger: http://localhost:8000/docs.
PostgreSQL и Redis не публикуют порты на хосте. Сервисы приложения доступны только локально.
Замените пример пароля в `.env` и согласуйте его с `DATABASE_URL`.
Backend пока не подключается к PostgreSQL; Celery имеет настроенный broker, но задач ещё нет.
Остановка: `docker compose --env-file .env -f infra/compose.yaml down`.

## Запуск без Docker
В двух терминалах, из корня:
```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```
```bash
cd frontend
npm ci
npm run dev
```
На Windows активация venv: `.venv\Scripts\activate`.

## Проверки
```bash
cd backend
python -m pytest
ruff check .
ruff format --check .
```
```bash
cd frontend
npm test
npm run build
```
CI повторяет эти проверки. Конфигурация окружения читается сервером; реальные секреты
не коммитить. `.env.example` содержит только примеры, административные ключи будут
реализованы позднее в соответствии с архитектурой.

## Следующий шаг
Завершить приёмку фазы 0 в Docker-окружении, затем выполнять фазу 1 по согласованному
плану. Этот каркас не восстанавливает код из прежнего чата или несохранённой ветки.
