# Ручной перенос резервного пакета

Основная история исходников хранится в GitHub после commit/push.
Пакет готовится после каждой завершённой фазы и перед изменениями среды.
Автоматическая синхронизация не настраивается.

```bash
python tools/make_export.py --checkout . --phase 6 --output ../knigoskop-manual-export
```

Каталог `--output` должен находиться вне checkout. Пример в облачной среде:
`/workspace/knigoskop-manual-export/latest/`.
Три постоянных имени: `Knigoskop-emergency-latest.zip`, `README-backup.md`, `SHA256SUMS.txt`.
Инструмент проверяет CRC ZIP и SHA-256 каждого файла и сохраняет Git SHA в EXPORT-MANIFEST.json.
Для идентификации версии Git перед экспортом должен быть чистым.

В этом чате пользователь успешно скачал исходный пакет по ссылкам `sandbox:` и подтвердил локальное сохранение.
Для следующего пакета Codex выдаёт ссылки на три существующих файла и отдельно указывает статус переноса.
Работа ссылки зависит от интерфейса; наличие файла в облаке не является подтверждением скачивания.

## Windows

Папка: `C:\проекты PRJ\книгоскоп\LocalArchCodex`.
Скачивать новую тройку в `_incoming` внутри этой папки. Старую проверенную копию сохранять до проверки новой.

```powershell
Set-Location 'C:\проекты PRJ\книгоскоп\LocalArchCodex\_incoming'
$expected = ((Get-Content -LiteralPath '.\SHA256SUMS.txt' -Raw).Trim() -split '\s+')[0]
$actual = (Get-FileHash -LiteralPath '.\Knigoskop-emergency-latest.zip' -Algorithm SHA256).Hash
if ($actual -ine $expected) { throw 'SHA-256 не совпадает; старую копию сохранить' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead((Join-Path (Get-Location) 'Knigoskop-emergency-latest.zip'))
try {
    if ($null -eq $zip.GetEntry('EXPORT-MANIFEST.json')) { throw 'Нет манифеста ZIP' }
} finally { $zip.Dispose() }
```

После совпадения SHA-256 и проверки манифеста заменить три прежних файла новыми.
Сообщить Codex build_id и результат проверки, чтобы отразить подтверждение в истории.
Проверка PowerShell не выполнялась в облаке; Windows-перенос выполняет пользователь.

## Границы пакета

Только исходники, миграции, доступные тесты, lock-файлы, документация и инструмент экспорта.
Дампы БД, Docker-образы, книги пользователей, секреты, журналы и зависимости не включаются.
Это дополнительная копия к GitHub, а не полная резервная копия работающего сервиса.
Полный аварийный архив сохраняется отдельно; его нельзя публиковать в GitHub.
