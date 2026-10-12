"""Create a small manual source export; no sync, Git writes or app changes."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import zipfile
from zoneinfo import ZoneInfo

NAME = "Knigoskop-emergency-latest.zip"
IGNORED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".cache",
    ".local",
    "dist",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "data",
    "volumes",
    "manual-export",
    "latest",
    "private",
    "transfer",
}
ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(
    rb"(sk_live_[A-Za-z0-9]{32}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def acceptable(name):
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts


def archive(recovery, name):
    manifest = json.loads((recovery / "public/manifest.json").read_text())
    row = next(r for r in manifest["records"] if r["file"] == "private/" + name)
    path = recovery / row["file"]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    if h.hexdigest() != row["sha256"]:
        raise RuntimeError("Recovery source checksum mismatch")
    return tarfile.open(path, "r:gz")


def recovered_files(recovery):
    files = {}
    for src, target in [
        ("knigoskop-api-1-app.tar.gz", "backend"),
        ("knigoskop-frontend-1-app.tar.gz", "frontend"),
    ]:
        with archive(recovery, src) as t:
            for entry in t:
                if not entry.isfile() or not acceptable(entry.name):
                    continue
                parts = PurePosixPath(entry.name).parts
                if not parts or parts[0] != "app":
                    continue
                rel = PurePosixPath(*parts[1:])
                if any(p in IGNORED for p in rel.parts) or str(rel).endswith(".pyc"):
                    continue
                if target == "backend":
                    keep = (
                        rel.parts
                        and rel.parts[0] in ("app", "alembic")
                        or str(rel)
                        in ("alembic.ini", "pyproject.toml", "requirements.lock")
                    )
                else:
                    keep = (
                        rel.parts
                        and rel.parts[0] == "src"
                        or str(rel)
                        in (
                            "package.json",
                            "package-lock.json",
                            "vite.config.ts",
                            "tsconfig.json",
                            "eslint.config.js",
                            "index.html",
                            "Dockerfile",
                            ".dockerignore",
                            ".prettierrc",
                            ".prettierignore",
                        )
                    )
                if keep:
                    files[target + "/" + str(rel)] = t.extractfile(entry).read()
    with archive(recovery, "specifications-attachments.tar.gz") as t:
        for entry in t:
            if entry.isfile() and PurePosixPath(entry.name).name in (
                "01_TECHNICAL_REQUIREMENTS.md",
                "02_SYSTEM_ARCHITECTURE.md",
                "03_CODEX_DEVELOPMENT_PLAN.md",
            ):
                files["docs/" + PurePosixPath(entry.name).name] = t.extractfile(
                    entry
                ).read()
    inventory = json.loads((recovery / "private/inventory.json").read_text())
    files["EXPORT-SOURCE-IMAGES.json"] = json.dumps(
        {
            "images": [
                i
                for i in inventory["images"]
                if any(tag.startswith("knigoskop-") for tag in i["tags"])
            ]
        },
        indent=2,
    ).encode()
    return files, {
        "mode": "recovered-source",
        "recovery": str(recovery),
        "completeness": "partial recovered phases 1-6",
        "missing": [
            "backend tests",
            "original setup/check/browser scripts",
            "complete current infrastructure configuration",
            "phase 7 source/reports",
            "RouterAI budget ledger",
        ],
        "test_status": "No application tests run for this export; historical results are not repeated verification.",
    }


def checkout_files(checkout):
    essential = [
        "backend/pyproject.toml",
        "frontend/package.json",
        "docs/01_TECHNICAL_REQUIREMENTS.md",
        "docs/02_SYSTEM_ARCHITECTURE.md",
        "docs/03_CODEX_DEVELOPMENT_PLAN.md",
    ]
    if any(not (checkout / p).is_file() for p in essential):
        raise RuntimeError("Incomplete checkout; existing export is left intact")
    files = {}
    listing = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
        cwd=checkout,
        capture_output=True,
    )
    git_files = (
        set(listing.stdout.decode().split("\0")) if listing.returncode == 0 else None
    )
    for folder, dirs, names in os.walk(checkout):
        dirs[:] = [
            d
            for d in dirs
            if d not in IGNORED
            and not d.endswith(".egg-info")
            and not (Path(folder) / d).is_symlink()
        ]
        for name in names:
            p = Path(folder) / name
            if (
                git_files is not None
                and p.relative_to(checkout).as_posix() not in git_files
            ):
                continue
            if p.is_symlink() or not p.is_file():
                continue
            if (name.startswith(".env") and name != ".env.example") or p.suffix in (
                ".log",
                ".dump",
                ".db",
                ".sqlite",
                ".sqlite3",
                ".zip",
                ".tar",
                ".gz",
                ".pyc",
            ):
                continue
            if p.stat().st_size > 20 * 1024 * 1024:
                raise RuntimeError("Large source file needs review")
            files[p.relative_to(checkout).as_posix()] = p.read_bytes()

    def git(*args):
        r = subprocess.run(
            ["git", *args],
            cwd=checkout,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        return r.stdout.strip() if r.returncode == 0 else None

    return files, {
        "mode": "checkout",
        "head": git("rev-parse", "--verify", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(git("status", "--porcelain")),
        "completeness": "source export; excludes runtime data and secrets",
        "test_status": "Use phase report for actual test results; export does not run tests or publish commits.",
    }


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--recovery", type=Path)
    group.add_argument("--checkout", type=Path)
    parser.add_argument("--phase", type=int, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT.parent / "knigoskop-manual-export",
        help="Export directory outside the checkout",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if args.checkout and (
        output == args.checkout.resolve() or args.checkout.resolve() in output.parents
    ):
        raise RuntimeError("Export directory must be outside the checkout")
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    files, provenance = (
        recovered_files(args.recovery)
        if args.recovery
        else checkout_files(args.checkout)
    )
    if not files:
        raise RuntimeError("No source files")
    secrets = [
        s.encode()
        for n in ["KNIGOSKOP_LLM_API_KEY", "KNIGOSKOP_TTS_API_KEY", "AI_MASTER_KEY"]
        if (s := os.environ.get(n, "")) and len(s) >= 16
    ]
    for name, data in files.items():
        if PATTERN.search(data) or any(s in data for s in secrets):
            raise RuntimeError(
                "Potential literal secret: review privately before export"
            )
    now = datetime.datetime.now(datetime.timezone.utc)
    build = now.strftime("%Y%m%dT%H%M%SZ")
    state_path = output / "export-state.json"
    state = (
        json.loads(state_path.read_text()) if state_path.exists() else {"history": []}
    )
    event = {
        "build_id": build,
        "phase": args.phase,
        "created_moscow": now.astimezone(ZoneInfo("Europe/Moscow")).isoformat(),
        "source_mode": provenance["mode"],
        "copied_to_windows_confirmed": False,
    }
    history = state["history"] + [event]
    readme = """# Книгоскоп — ручное аварийное сохранение

Основная история проекта — GitHub после commit/push. Сохранение среды Codex не является отправкой в GitHub.
Локальная папка: C:\\проекты PRJ\\книгоскоп\\LocalArchCodex

Постоянные имена: Knigoskop-emergency-latest.zip, README-backup.md, SHA256SUMS.txt.
Готовить пакет после каждой законченной фазы и перед заменой/публикацией среды либо рискованным изменением.
Codex предлагает пакет; пользователь переносит его вручную. Автоматической синхронизации нет.
Наличие файла в облаке не подтверждает копию на Windows. В этом чате пользователь подтвердил скачивание по ссылкам sandbox:.

## Замена на Windows

Скачивать новую тройку файлов в _incoming внутри той же локальной папки проекта.
Проверить SHA-256 ZIP по новому SHA256SUMS.txt, затем прочитать ZIP и внутренний EXPORT-MANIFEST.json.
Только после успеха заменить прежние три файла актуальными; прежняя копия должна оставаться до проверки новой.
Не удалять последний проверенный пакет до успешной замены. Историю сохранять в README, версии внутри ZIP — по build_id/Git SHA.
Подтверждать перенос отдельно: дата, build_id, размер и совпавшая SHA-256. До этого статус: только облачная копия.

```powershell
Get-FileHash -LiteralPath 'C:\\проекты PRJ\\книгоскоп\\LocalArchCodex\\_incoming\\Knigoskop-emergency-latest.zip' -Algorithm SHA256
```

## Состав и ограничения

Пакет содержит исходники, доступные тесты, миграции, lockfiles, документацию и экспортный инструмент.
Runtime ключи/.env, персональные книги, дампы, журналы, Docker images/volumes, node_modules и caches не включаются.
Это компактный source export. Данные БД и файлов хранятся отдельной аварийной копией по необходимости.
Первая копия восстановлена из Docker фаз 1–6: прежние backend tests, scripts, полная инфраструктура, фаза 7 и budget ledger не найдены.
Последующие пакеты содержат восстановленный checkout; фактические проверки и ограничения указаны в docs/04_RECOVERY_REPORT.md.
Оригинальная большая аварийная копия сохраняется до подтверждённого независимого переноса и восстановления.
Пакет не устанавливает зависимости, не запускает приложение и не подтверждает его тесты.

## История подготовки

"""
    readme += (
        f"Текущий build: {build}; фаза: {args.phase}; режим: {provenance['mode']}.\n\n"
    )
    for e in history:
        copied = (
            "перенос на Windows подтверждён пользователем"
            if e.get("copied_to_windows_confirmed")
            else "перенос на Windows не подтверждён"
        )
        readme += f"- {e['created_moscow']}: фаза {e['phase']}, build {e['build_id']}; {copied}.\n"
    files["README-backup.md"] = readme.encode()
    files["tools/make_export.py"] = Path(__file__).read_bytes()
    manifest = {
        "format_version": 1,
        "build_id": build,
        "phase": args.phase,
        "created_utc": now.isoformat(),
        "provenance": provenance,
        "independent_storage_confirmed": False,
        "files": [
            {"path": n, "size_bytes": len(d), "sha256": sha(d)}
            for n, d in sorted(files.items())
        ],
    }
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=output))
    package = staging / NAME
    with zipfile.ZipFile(
        package, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as z:
        for name, data in sorted(files.items()):
            z.writestr(name, data)
        z.writestr(
            "EXPORT-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    with zipfile.ZipFile(package) as z:
        if z.testzip() is not None:
            raise RuntimeError("ZIP CRC mismatch")
        for row in manifest["files"]:
            data = z.read(row["path"])
            if len(data) != row["size_bytes"] or sha(data) != row["sha256"]:
                raise RuntimeError("Source member SHA mismatch")
    digest = sha(package.read_bytes())
    event["archive_sha256"] = digest
    with (staging / "README-backup.md").open("x", encoding="utf-8") as f:
        f.write(readme + "\nSHA-256 текущего ZIP: " + digest + "\n")
    with (staging / "SHA256SUMS.txt").open("x") as f:
        f.write(digest + "  " + NAME + "\n")
    latest = output / "latest"
    latest.mkdir(mode=0o700, exist_ok=True)
    for name in [NAME, "README-backup.md", "SHA256SUMS.txt"]:
        os.replace(staging / name, latest / name)
    state_new = staging / "state.json"
    with state_new.open("x", encoding="utf-8") as f:
        json.dump(
            {"history": history, "latest_build": build}, f, ensure_ascii=False, indent=2
        )
    os.replace(state_new, state_path)
    print(
        json.dumps(
            {
                "build": build,
                "archive": str(latest / NAME),
                "bytes": (latest / NAME).stat().st_size,
                "sha256": digest,
                "source_members": len(files),
                "zip_crc_and_member_sha256": True,
                "windows_copy_confirmed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
