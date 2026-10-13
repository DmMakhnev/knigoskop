# Тестовый сервер: создание и запуск

Стенд размещается отдельно от аварийной среды. Покупка VPS и домена выполняется
вами: в Codex сейчас нет вашего сервера, IP, домена или доступа SSH.
Команды ниже предназначены для **нового пустого Ubuntu 24.04 LTS**, не для
действующего окружения восстановления. Сначала сверяйте адрес машины.

## 1. Создать VPS и домен

1. В панели выбранного хостинга создайте VPS с Ubuntu 24.04 LTS, публичным IPv4,
   минимум 2 vCPU, 4 ГБ RAM и 40 ГБ SSD. Для сборки удобно 8 ГБ RAM.
   Выбирайте регион и стоимость самостоятельно; автоматических покупок нет.
2. В PowerShell Windows создайте отдельный SSH-ключ, если подходящего ещё нет:
   `ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\knigoskop_staging"`.
   Установите пароль ключа. В панель VPS загрузите только файл
   `knigoskop_staging.pub`; приватный файл без `.pub` остаётся на компьютере.
3. Создайте DNS-запись типа A для вашего поддомена, например `test.ваш-домен`,
   со значением публичного IPv4 VPS. Это пример имени, не действующий адрес.
   AAAA добавляйте только при настроенном IPv6. На первом запуске отключите
   внешнее проксирование DNS. Дождитесь разрешения имени в правильный IP.
4. В сетевом firewall хостинга разрешите TCP 80 и 443 для всех; TCP 22 —
   с вашего IP, если он постоянный. Не открывайте 5432, 6379, 8000, 15173, 18000.
5. Сохраните IP, SSH-имя пользователя из панели и выбранный hostname.
   Для продолжения можно сообщить эти три несекретных параметра.
   Пароли, приватные ключи и API-ключи в чат не отправляйте.

Проверка с Windows: `Resolve-DnsName ВАШ_HOSTNAME`.
Подключение (замените пользователь/IP фактическими):

```powershell
ssh -i "$env:USERPROFILE\.ssh\knigoskop_staging" USER@PUBLIC_IP
```

При первом подключении сверяйте fingerprint SSH с панелью хостинга.
Не отключайте проверку host key.

## 2. Подготовить новый Ubuntu

После SSH перейдите в root через `sudo -i`, если вошли обычным пользователем.
Следующие команды требуют root. Проверьте, что это именно новый VPS:

```bash
hostname
cat /etc/os-release
df -h /
apt-get update
apt-get install -y ca-certificates git python3 docker.io docker-compose-v2 ufw
systemctl enable --now docker
docker version
docker compose version
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Не закрывайте SSH, пока не проверили вход во второй сессии. Docker-публикация
портов может обходить UFW; поэтому ограничение в firewall хостинга тоже нужно.
В staging Compose опубликованы только 80/443.

```bash
git clone https://github.com/DmMakhnev/knigoskop.git /opt/knigoskop
cd /opt/knigoskop
git branch --show-current
git log -1 --oneline
```

Если репозиторий приватный, настройте в GitHub отдельный read-only deploy key
для этого VPS и используйте SSH clone. Не вставляйте GitHub token в URL/чат.
Нельзя копировать аварийные рабочие тома на новый сервер вместо создания БД.

## 3. Создать приватные настройки и запустить

Сначала создайте пароль внешнего входа на стенд. Это отдельный пароль, он не
заменяет аккаунт приложения. Caddy спросит его интерактивно:

```bash
docker run --rm -it caddy:2-alpine caddy hash-password
python3 tools/init_staging_env.py --domain ВАШ_HOSTNAME
```

Второй инструмент попросит полученный bcrypt-хеш без отображения ввода,
создаст `.env.staging` с правами 0600, сгенерирует пароли БД и `AI_MASTER_KEY`.
Существующий файл он не перезаписывает. Хеш и все секреты остаются на VPS.
Сохраните `.env.staging` отдельно: потеря master key делает зашифрованные
API-ключи в БД недоступными. При обновлении ключ не генерируют заново.

```bash
bash scripts/staging.sh config
bash scripts/staging.sh build
bash scripts/staging.sh start
bash scripts/staging.sh status
docker compose --env-file .env.staging -f infra/compose.staging.yaml exec api \
  python -m app.cli create-admin --email ВАШ_EMAIL
```

Пароль администратора задаётся интерактивно. Caddy получает публичный
сертификат автоматически, когда DNS указывает на VPS и 80/443 доступны.
После этого откройте `https://ВАШ_HOSTNAME`: внешний логин `reviewer`, пароль
из `hash-password`; затем войдите в приложение созданным администратором.
Это точный шаблон будущего адреса; опубликованного стенда пока нет.

Повторный запуск: `cd /opt/knigoskop && bash scripts/staging.sh start`.
После перезагрузки VPS сервисы поднимаются Docker через `unless-stopped`.
`/health` — liveness; `/ready` проверяет БД, Redis и миграцию `0009_admin`.
Оба адреса тоже защищены внешним паролем. Проверка с запросом пароля:

```bash
curl --fail --user reviewer https://ВАШ_HOSTNAME/ready
docker compose --env-file .env.staging -f infra/compose.staging.yaml logs --tail=50 proxy
```

Не применяйте `curl -k`. При ошибке сертификата проверяйте DNS, доступность
портов и журнал Caddy. Журналы просматривайте приватно, не публикуйте целиком.

## 4. AI, тестовые маршруты и почта

Первый запуск не требует AI-ключей. Для бесплатной проверки в **новой БД**
можно явно создать mock-маршруты:

```bash
docker compose --env-file .env.staging -f infra/compose.staging.yaml exec api \
  python -m app.domains.summaries.cli seed-summary-mock
docker compose --env-file .env.staging -f infra/compose.staging.yaml exec api \
  python -m app.domains.audio.cli seed-audio-mock
```

Mock-аудио — сигналы, не речь. Mock-маршруты резервируют worst-case расход
токенов: если запуск возвращает `quota_exceeded`, проверьте квоты конкретного
тестового пользователя в админке. Не снимайте ограничения для всех аккаунтов.
В админке можно вводить реальные ключи через
HTTPS: сервер шифрует их master key и не возвращает значения. Перед включением
реальных маршрутов отдельно согласуйте тест/бюджет. Эти команды ничего не
списывают. Старые балансы/данные не переносятся автоматически.

По умолчанию `MAIL_BACKEND=disabled`, `REGISTRATION_ENABLED=false`:
регистрации и email-восстановления нет, администратор создаётся CLI.
Для регистрации задайте в приватном `.env.staging`:
`MAIL_BACKEND=smtp`, `REGISTRATION_ENABLED=true`, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_TLS=starttls` (обычно 587) либо `ssl` (обычно 465), `SMTP_USERNAME`,
`SMTP_PASSWORD`, `SMTP_FROM`. Настройте SPF/DKIM/DMARC у почтового провайдера.
Перезапустите `bash scripts/staging.sh start`, проверьте доставку настоящего
письма. До этой проверки SMTP считается подготовленным, но не принятым.
Не используйте файловые dev-письма в production.

## 5. Резервное копирование

Исходный ZIP/GitHub не содержит книг, БД или ключей. Для данных используется
отдельный приватный maintenance backup: API/worker остановлены, PostgreSQL
работает; `pg_dump -Fc` + роли + стабильные файлы + `.env.staging` + SHA-256.
Redis не копируется: durable jobs хранятся в PostgreSQL.
Архивы конфиденциальны, в GitHub их не отправлять.

Ручной запуск согласованного окна обслуживания на **этом staging**, не в
аварийной среде:

```bash
bash scripts/ops/nightly_backup.sh
```

Скрипт временно останавливает API/worker и возвращает ранее работавшие сервисы
даже при ошибке. Результат: `/var/backups/knigoskop/UTC_TIMESTAMP/`.
Для автоматического ежедневного окна около 03:15 по Москве:

```bash
cp infra/systemd/knigoskop-backup.service /etc/systemd/system/
cp infra/systemd/knigoskop-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now knigoskop-backup.timer
systemctl list-timers knigoskop-backup.timer
```

Таймер на текущем облачном окружении не включался. Старые копии автоматически
не удаляются; проверяйте `df -h` и переносите результаты вне VPS.
Для скачивания на Windows при SSH под root (иначе согласуйте права отдельно):

```powershell
scp -i "$env:USERPROFILE\.ssh\knigoskop_staging" -r root@PUBLIC_IP:/var/backups/knigoskop/UTC_TIMESTAMP "C:\проекты PRJ\книгоскоп\LocalArchCodex"
```

Это чувствительные данные, ограничьте доступ к папке Windows. Проверяйте
`Get-FileHash -Algorithm SHA256` для каждого файла по `manifest.json`.
Копия, оставшаяся только на VPS/Codex, не считается независимым сохранением.

## 6. Восстановление и обновление

`python3 scripts/ops/backup_restore.py verify --backup PATH` проверяет размеры,
SHA-256 и допустимые члены tar. Восстановление разрешено только в пустой
отдельный Docker project `knigoskop-restore-*`: fresh PostgreSQL с тем же
app-role, пустой files volume и созданный, но не запущенный API.
На отдельной машине подготовьте Compose/env с нужным hostname; при одном VPS
задайте другую web-подсеть и доверенный IP Caddy, чтобы исключить пересечение.
Используйте master key из приватной копии, не новый ключ.

```bash
docker compose --env-file PRIVATE_ENV -f PRIVATE_COMPOSE -p knigoskop-restore-check up -d postgres redis
docker compose --env-file PRIVATE_ENV -f PRIVATE_COMPOSE -p knigoskop-restore-check create api
python3 scripts/ops/backup_restore.py restore --env PRIVATE_ENV \
  --compose PRIVATE_COMPOSE --project knigoskop-restore-check --backup PATH
```

Не запускайте миграции до restore: целевая БД должна быть пустой. Скрипт
отказывается восстанавливать поверх заполненных таблиц/файлов, не запускает
сервисы. `roles.sql` сохранён для справки; restore использует созданную
app-role и не перезаписывает глобальные роли действующей PostgreSQL.
Проверку HTTPS и приложения после restore выполняют отдельно.

Перед обновлением: независимая проверенная копия → записать Git SHA/теги
образов → согласовать окно → остановить API/worker → `git pull --ff-only`
при чистом checkout → новый `RELEASE_TAG` в приватной конфигурации → build →
start (включает миграции) → `/ready` и функциональная проверка.
Не заменяйте `.env.staging`, не удаляйте тома/старые образы. Откат кода после
необратимой миграции требует восстановления в отдельную среду, а не `reset`.

## Архитектурное решение

На staging собранный React обслуживает Caddy, dev Vite отсутствует.
Python API, worker, PostgreSQL и Redis остаются отдельными сервисами.
API/worker непривилегированные с read-only rootfs; app-role не superuser.
Только Caddy имеет внешний порт и право bind; доверенный proxy IP фиксирован.
Basic Authorization удаляется при передаче API; приложение использует свою
сессию и CSRF. Логи ограничены по объёму, API/access-логи выключены.
Docker socket не доступен контейнерам приложения. `/ready` не проверяет
самостоятельно heartbeat worker; отсутствие обработки контролируется по
состоянию задач/журналам. Внешний мониторинг и SMTP нужно проверить на VPS.
