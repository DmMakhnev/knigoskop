# Книгоскоп — техническая архитектура

Версия 1.0. Согласована с `01_TECHNICAL_REQUIREMENTS.md`.

## 1. Технологический стек
- Frontend: React + TypeScript + Vite, React Router, TanStack Query, доступная библиотека UI-компонентов.
- Backend: Python 3.12+ / FastAPI / Pydantic v2 / SQLAlchemy 2 / Alembic.
- База данных: PostgreSQL; миграции Alembic.
- Очереди и кэш: Redis + Celery (worker + beat при необходимости).
- Файлы: объектное хранилище через абстракцию `StorageBackend` (в MVP локальный Docker volume; в будущем S3-совместимый backend).
- Reverse proxy: Caddy с автоматическим HTTPS либо Nginx с управляемыми сертификатами; для MVP предпочтителен Caddy.
- Деплой: Docker Compose на Ubuntu 24.04.
- Тесты: pytest, httpx, pytest-asyncio (по необходимости), Vitest/React Testing Library, Playwright.

## 2. Логическая схема
```text
Browser (React) --HTTPS--> Reverse proxy --> FastAPI API
                                           |--> PostgreSQL
                                           |--> Redis (broker/cache)
                                           |--> StorageBackend
                                           +--> Celery workers --> AI provider adapters
                                                               --> TTS adapters
                                                               --> Source connectors
```

Веб-запросы не выполняют долгие AI/TTS-операции. Backend регистрирует `job`, worker исполняет; UI получает статус через polling (MVP), возможен SSE в будущем. Backend — единственная точка доступа к секретам и внешним провайдерам.

## 3. Предлагаемая структура репозитория
```text
knigoskop/
  docs/
    01_TECHNICAL_REQUIREMENTS.md
    02_SYSTEM_ARCHITECTURE.md
    03_CODEX_DEVELOPMENT_PLAN.md
  backend/
    app/
      api/v1/
      core/          # config, security, errors, logging
      db/            # models, session, migrations helpers
      domains/       # auth, users, library, books, summaries, audio, admin, usage
      integrations/  # sources, llm, tts, email, storage
      workers/       # tasks, queue policy
      main.py
    alembic/
    tests/
    pyproject.toml
  frontend/
    src/
      app/
      pages/
      features/
      components/
      api/
      styles/
    package.json
  infra/
    compose.yaml
    Caddyfile
  scripts/
  .env.example
  README.md
```

## 4. Данные и связи
Ниже логические сущности, окончательные SQL-типы и индексы — в миграциях.

| Таблица | Ключевые поля | Назначение |
|---|---|---|
| `users` | id, email(unique), password_hash, role, status, verified_at, created_at | Учётные записи |
| `auth_tokens` | id, user_id, token_hash, kind, expires_at, used_at | Подтверждение и сброс |
| `works` | id, title, author, language, metadata | Произведение как библиографическая сущность |
| `editions` | id, work_id, edition_label, translator, source_id, rights_status | Издание/перевод |
| `text_versions` | id, edition_id, checksum, storage_key, normalized_text_key, visibility, owner_user_id, created_at | Неизменяемый текст; приватные версии изолированы |
| `text_sections` | id, text_version_id, parent_id, kind, title, order_index, start_offset, end_offset | Части/главы/эпизоды |
| `library_items` | id, user_id, text_version_id, added_at, favorite, folder_id | Личное добавление без копирования |
| `library_folders` | id, user_id, name | Папки |
| `reading_progress` | user_id, library_item_id, section_id, offset, updated_at | Позиция чтения |
| `jobs` | id, user_id, type, status, progress, idempotency_key, error_code, created_at | Фоновые задания |
| `summaries` | id, text_version_id, level, model_config_version_id, prompt_version_id, status, content_hash | Версионированный результат |
| `summary_nodes` | id, summary_id, parent_id, text, order_index, source_section_id | Иерархия резюме |
| `summary_source_spans` | id, summary_node_id, text_version_id, start_offset, end_offset | Точная ссылка на оригинал |
| `summary_validations` | id, summary_id, validator_model_id, findings_json, status | AI-проверка |
| `audio_assets` | id, text_hash, provider_model_id, voice_id, storage_key, duration_ms, status, access_scope | Кэш TTS |
| `audio_word_timings` | id, audio_asset_id, word_index, word, start_ms, end_ms, text_start, text_end | Караоке |
| `audio_progress` | user_id, audio_asset_id, position_ms, playback_rate, updated_at | Прослушивание |
| `providers` | id, name, kind, protocol, base_url, enabled | LLM/TTS-провайдер |
| `provider_credentials` | id, provider_id, encrypted_secret, key_version | Шифрованные ключи |
| `provider_models` | id, provider_id, model_identifier, capabilities_json, enabled | Модели |
| `task_model_routes` | id, task_type, primary_model_id, fallback_model_id, config_version | Маршрутизация |
| `prompt_templates` | id, task_type, name | Шаблоны |
| `prompt_versions` | id, template_id, version, body, variables_schema, status, created_by | История промптов |
| `usage_events` | id, user_id, job_id, provider_model_id, units_json, estimated_cost, currency | Учёт расходов |
| `quota_policies` | id, scope, metric, period, limit_value | Глобальные лимиты |
| `quota_overrides` | id, user_id, metric, period, limit_value | Индивидуальные исключения |
| `audit_events` | id, actor_id, action, entity_type, entity_id, metadata_json, created_at | Админ-аудит |

Обязательные ограничения: уникальность `(user_id, text_version_id)` для `library_items`, индексы `(user_id, status)` у задач, внешние ключи, ограничения диапазонов `start_offset < end_offset`, `start_ms <= end_ms`. Не хранить приватный исходный текст в открытом журнале. Для общих кэшей права доступа проверять на каждый запрос.

## 5. API (ориентировочные контракты)
Все маршруты под `/api/v1`, JSON, пагинация, единый формат ошибок `{code, message, request_id}`.

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/auth/register`, `/auth/login`, `/auth/logout` | Регистрация/вход/выход |
| POST | `/auth/verify-email`, `/auth/request-password-reset`, `/auth/reset-password` | Email-процессы |
| GET/DELETE | `/me`, `/me` | Профиль / удаление аккаунта (с подтверждением) |
| GET | `/books/search?q=` | Каталог и разрешённые источники |
| POST | `/books/import`, `/books/upload` | Импорт/загрузка |
| GET/POST/DELETE | `/library`, `/library`, `/library/{item_id}` | Личная библиотека |
| GET | `/texts/{version_id}/sections/{section_id}` | Авторизованный текст раздела |
| POST | `/library/{item_id}/process` | Запуск обработки |
| GET | `/jobs/{job_id}` | Статус/прогресс |
| GET | `/library/{item_id}/summaries?level=` | Результаты |
| POST | `/audio/generate` | Запуск TTS |
| GET | `/audio/{asset_id}/manifest`, `/audio/{asset_id}/stream` | Аудио и таймкоды с ACL |
| PUT | `/me/reading-progress`, `/me/audio-progress` | Прогресс |
| GET/POST/PATCH | `/admin/providers`, `/admin/models`, `/admin/routes` | Настройки AI |
| GET/POST/PATCH | `/admin/prompts`, `/admin/quotas`, `/admin/users` | Управление |
| GET | `/admin/usage`, `/admin/jobs`, `/admin/audit` | Статистика/диагностика |

Для административных ресурсов реализовать стандартные детализированные маршруты с идентификаторами, а не один универсальный endpoint. Проверять владение объектом в каждом endpoint; не доверять `user_id` из тела запроса.

## 6. Пайплайн обработки текста
1. `ingest`: проверка типа/размера, безопасное извлечение, права и метаданные.
2. `normalize`: кодировка, абзацы, заголовки, карта оригинальных смещений, checksum.
3. `structure`: детекция частей и глав, разделение длинных разделов на чанки с перекрытием; стабильные диапазоны.
4. `summarize_leaf`: генерация фактов/эпизодов для каждого чанка со ссылками на исходные диапазоны.
5. `aggregate`: главы, части, три уровня резюме; не терять идентификаторы источников.
6. `validate`: отдельный проход проверки с фактами и диапазонами; детерминированная проверка JSON-схемы, диапазонов и принадлежности текстовой версии.
7. `publish`: атомарное переключение статуса на `completed`; прежняя опубликованная версия сохраняется до успешной замены.

Каждый шаг идемпотентен, сохраняет промежуточные артефакты и диагностику. Конкретная LLM не должна влиять на доменную модель. AI может предложить диапазон, но backend проверяет его и, если нужно, помечает ссылку как неподтверждённую.

## 7. Адаптеры провайдеров
Интерфейсы: `LLMProvider.generate_structured(...)`, `TTSProvider.synthesize(...)`, `SourceConnector.search(...)`, `SourceConnector.fetch(...)`. Возможности объявляются явно (`supports_json_schema`, `supports_word_timings`, `supports_voices`, `max_context_tokens`). Универсальный адаптер только для совместимых протоколов; нестандартные API — отдельные классы.

`ProviderRouter` читает версионированный маршрут задачи; fallback выполняется при классифицированной технической ошибке (тайм-аут, rate limit, недоступность), а не при некорректном содержании без отдельной политики. Ограничить повторы, бюджет и время; сохранять фактически использованную модель и расход.

Секреты: шифрование с master key из окружения/секрет-хранилища, никогда не возвращать расшифрованное значение клиенту; маска — только отображение. Тест подключения не раскрывает ключ.

## 8. Аудио и синхронизация
Генерация TTS разбивается на устойчивые сегменты, к каждому сохраняются аудиофайл, текст, хеш, voice/model config и временные метки. Если TTS не возвращает word timings, выполнить forced alignment отдельным модулем и сохранить показатель уверенности. При недостаточной точности — подсветка предложений или отключение режима с сообщением. Плеер берёт текущую позицию из `HTMLMediaElement.currentTime`, применяет таймкоды и прокручивает текст. Нажатие на фразу переводит позицию на соответствующий таймкод. Изменение `playbackRate` не меняет сами таймкоды.

## 9. Безопасность и права
- Cookie с `HttpOnly`, `Secure`, `SameSite` и CSRF-защита при cookie-аутентификации; ограничение срока сессий.
- Пароли Argon2id; rate limiting входа/регистрации; безопасные одноразовые email-токены.
- RBAC (`user/admin`) и объектные ACL на текст, резюме, аудио, задачи и прогресс.
- SSRF-защита для URL провайдеров и импортируемых источников: разрешённые схемы/домены, блокирование private/link-local/metadata IP, проверка редиректов и DNS-resolve, сетевые политики.
- Лимиты и сканирование загружаемых файлов, защита от zip bomb, path traversal, HTML/script в EPUB/FB2.
- Не включать приватный текст в внешние аналитические сервисы; уведомлять пользователя о передаче текста AI-провайдеру.
- Audit log для админских изменений без секретов и содержимого книг.

## 10. Развёртывание и эксплуатация
Compose-сервисы: `proxy`, `frontend`, `api`, `worker`, `postgres`, `redis`; тома для БД и файлов. `backend` применяет Alembic migrations отдельной одноразовой командой до запуска; создание первого администратора — явный CLI с безопасным вводом пароля. `.env.example` без ключей. Healthchecks, restart policy, ограничения ресурсов, лог-ротация. Ежедневный backup PostgreSQL + файлов/метаданных, шифрование копий и проверка восстановления. Мониторинг ошибок, доступности, заполнения диска, очередей и расходов. Для небольшого VDS контролировать RAM при одновременной обработке PDF и AI.

## 11. Архитектурные решения (ADR)
- ADR-001: единая библиографическая сущность и отдельные версии текстов — для правильной дедупликации.
- ADR-002: личная библиотека — связь с версией текста, а не дублирование содержимого.
- ADR-003: приватный контент никогда не становится публичным автоматически.
- ADR-004: фоновые операции — через очередь, API остаётся отзывчивым.
- ADR-005: результаты и промпты версионируются; опубликованное не перезаписывается незаметно.
- ADR-006: права на исходный текст определяют доступ к производным резюме и аудио.
- ADR-007: караоке требует реальных или проверенных выровненных таймкодов.
