"""Run recovered code on existing images in a private, disposable Docker stack."""

import argparse
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
ENV = os.environ.copy()
for selector in (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
):
    ENV.pop(selector, None)
LIVE = [
    f"knigoskop-{service}-1"
    for service in ("api", "worker", "frontend", "postgres", "redis")
]


def docker(*args, check=True):
    result = subprocess.run(
        DOCKER + list(args), env=ENV, capture_output=True, text=True
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Docker operation {args[0]} failed: {result.stderr.strip()}"
        )
    return result


def live_state():
    result = docker(
        "inspect",
        "--format",
        "{{.Id}} {{.Image}} {{.State.StartedAt}} {{.State.Running}}",
        *LIVE,
        check=False,
    )
    return result.stdout.splitlines()


def wait_ready(name, command):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if docker("exec", name, *command, check=False).returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError(f"Readiness check failed for {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-image", default="knigoskop-api:latest")
    parser.add_argument("--frontend-image", default="knigoskop-frontend:latest")
    args = parser.parse_args()
    for image in (
        args.api_image,
        args.frontend_image,
        "postgres:17-alpine",
        "redis:7-alpine",
    ):
        docker("image", "inspect", "--format", "{{.Id}}", image)
    before = live_state()
    run_id = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    prefix = "knigoskop-validation-" + run_id.lower()
    directory = ROOT / ".cache" / "validation" / run_id
    directory.mkdir(parents=True, mode=0o700)
    for folder in ("files", "mail"):
        (directory / folder).mkdir(mode=0o700)
    password = secrets.token_urlsafe(24)
    env_file = directory / "validation.env"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=test",
                "KNIGOSKOP_VALIDATION=1",
                "POSTGRES_DB=knigoskop_validation",
                "POSTGRES_USER=validation",
                f"POSTGRES_PASSWORD={password}",
                "POSTGRES_HOST=postgres",
                "REDIS_URL=redis://redis:6379/0",
                "AI_ALLOWED_HOSTS=[]",
                "AI_MASTER_KEY=",
                "KNIGOSKOP_LLM_API_KEY=",
                "SESSION_COOKIE_SECURE=false",
                "PUBLIC_BASE_URL=http://frontend:5173",
                'ALLOWED_ORIGINS=["http://frontend:5173"]',
                "STORAGE_DIRECTORY=/validation/files",
                "MAIL_DIRECTORY=/validation/mail",
                "QUOTA_DAILY_REQUESTS=1000",
                "QUOTA_DAILY_TOKENS=1000000",
                "QUOTA_DAILY_INPUT_CHARS=1000000",
                "AUTH_IP_RATE_LIMIT=1000",
                "REGISTER_RATE_LIMIT=100",
                "LOGIN_RATE_LIMIT=100",
                "JOB_RECOVERY_SECONDS=2",
                "PYTHONDONTWRITEBYTECODE=1",
                "NO_PROXY=api,frontend,postgres,redis,localhost,127.0.0.1",
            ]
        )
        + "\n"
    )
    env_file.chmod(0o600)
    names = []
    report = {"run_id": run_id, "image_builds": False, "paid_calls": False}
    label = "knigoskop.validation=" + run_id
    network_created = False
    tests_exit = 1

    def run(service, image, command, *extra):
        name = prefix + "-" + service
        docker(
            "run",
            "--pull=never",
            "-d",
            "--name",
            name,
            "--label",
            label,
            "--network",
            prefix,
            "--network-alias",
            service,
            *extra,
            image,
            *command,
        )
        names.append(name)
        return name

    app_mounts = [
        "--env-file",
        str(env_file),
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={ROOT / 'backend'},dst=/app,readonly",
        "--mount",
        f"type=bind,src={directory / 'files'},dst=/validation/files",
        "--mount",
        f"type=bind,src={directory / 'mail'},dst=/validation/mail",
        "--workdir",
        "/app",
    ]
    try:
        docker("network", "create", "--internal", "--label", label, prefix)
        network_created = True
        pg = run(
            "postgres",
            "postgres:17-alpine",
            [],
            "--env-file",
            str(env_file),
            "--memory=512m",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=384m",
        )
        redis = run(
            "redis",
            "redis:7-alpine",
            ["redis-server", "--save", "", "--appendonly", "no"],
            "--memory=128m",
            "--tmpfs",
            "/data:rw,size=64m",
        )
        wait_ready(pg, ["pg_isready", "-U", "validation", "-d", "knigoskop_validation"])
        wait_ready(redis, ["redis-cli", "ping"])
        migrate = docker(
            "run",
            "--rm",
            "--pull=never",
            "--network",
            prefix,
            "--label",
            label,
            *app_mounts,
            args.api_image,
            "alembic",
            "upgrade",
            "head",
        )
        (directory / "migrations.log").write_text(migrate.stdout + migrate.stderr)
        for module, command in (
            ("app.domains.summaries.cli", "seed-summary-mock"),
            ("app.domains.library.cli", "seed-demo"),
        ):
            docker(
                "run",
                "--rm",
                "--pull=never",
                "--network",
                prefix,
                "--label",
                label,
                *app_mounts,
                args.api_image,
                "python",
                "-m",
                module,
                command,
            )
        api = run(
            "api",
            args.api_image,
            ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
            *app_mounts,
            "--memory=768m",
        )
        run(
            "worker",
            args.api_image,
            [
                "celery",
                "-A",
                "app.workers.celery_app:celery_app",
                "worker",
                "--loglevel=warning",
                "--beat",
                "--schedule=/tmp/celerybeat-validation",
            ],
            *app_mounts,
            "--memory=768m",
        )
        wait_ready(
            api,
            [
                "python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')",
            ],
        )
        run(
            "frontend",
            args.frontend_image,
            ["npm", "run", "dev", "--", "--port", "5173"],
            "--memory=512m",
            "-e",
            "API_PROXY_TARGET=http://api:8000",
            "-e",
            "NO_PROXY=api,frontend",
            "--mount",
            f"type=bind,src={ROOT / 'frontend/src'},dst=/app/src,readonly",
            "--mount",
            f"type=bind,src={ROOT / 'frontend/vite.config.ts'},dst=/app/vite.config.ts,readonly",
        )
        result = docker(
            "run",
            "--rm",
            "--pull=never",
            "--network",
            prefix,
            "--label",
            label,
            *app_mounts,
            "--memory=768m",
            args.api_image,
            "python",
            "-m",
            "pytest",
            "integration_tests",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            check=False,
        )
        tests_exit = result.returncode
        (directory / "pytest.log").write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, flush=True)
        report["tests_exit_code"] = tests_exit
    finally:
        for name in names:
            logs = docker("logs", name, check=False)
            (directory / (name + ".log")).write_text(logs.stdout + logs.stderr)
            docker("rm", "--force", name, check=False)
        if network_created:
            docker("network", "rm", prefix, check=False)
        report["live_containers_unchanged"] = before == live_state()
        report["live_before"] = before
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps({"report": str(directory / "report.json"), **report}), flush=True
        )
    if not report["live_containers_unchanged"]:
        raise RuntimeError("Original container state changed during validation")
    raise SystemExit(tests_exit)


if __name__ == "__main__":
    main()
