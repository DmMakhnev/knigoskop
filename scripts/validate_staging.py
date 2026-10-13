"""Disposable HTTPS/static-app/backup-restore validation; no external AI calls."""

import base64
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import urlsplit, parse_qs
import httpx
from validate_recovery import live_state

ROOT = Path(__file__).resolve().parents[1]
ENV = os.environ.copy()
for key in [
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
]:
    ENV.pop(key, None)
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
DIAGNOSTICS = None


def run(args, input=None, check=True):
    result = subprocess.run(args, env=ENV, input=input, capture_output=True, text=True)
    if check and result.returncode:
        if DIAGNOSTICS:
            path = DIAGNOSTICS / "failure.log"
            path.write_text(result.stdout + "\n" + result.stderr)
            path.chmod(0o600)
        raise RuntimeError("Command failed: " + args[0] + "; private logs withheld")
    return result


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    project = "knigoskop-staging-test-" + stamp.lower()
    target = "knigoskop-restore-" + stamp.lower()
    directory = ROOT / ".cache/staging" / stamp
    directory.mkdir(parents=True, mode=0o700)
    global DIAGNOSTICS
    DIAGNOSTICS = directory
    mail = directory / "mail"
    mail.mkdir(mode=0o777)
    mail.chmod(0o777)
    envfile = directory / "test.env"
    gate_password = secrets.token_urlsafe(20)
    gate_hash = run(
        DOCKER
        + [
            "run",
            "--rm",
            "--pull=never",
            "caddy:2-alpine",
            "caddy",
            "hash-password",
            "--plaintext",
            gate_password,
        ]
    ).stdout.strip()
    variables = {}
    for line in (ROOT / "infra/staging.env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            variables[k] = v
    variables.update(
        COMPOSE_PROJECT_NAME=project,
        STAGING_DOMAIN="localhost:18443",
        STAGING_GATE_HASH=gate_hash,
        POSTGRES_ADMIN_PASSWORD=secrets.token_urlsafe(24),
        APP_POSTGRES_PASSWORD=secrets.token_urlsafe(24),
        AI_MASTER_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        AI_ALLOWED_HOSTS="[]",
        APP_ENV="test",
        MAIL_BACKEND="file",
        REGISTRATION_ENABLED="true",
        PUBLIC_BASE_URL="https://localhost:18443",
        ALLOWED_ORIGINS='["https://localhost:18443"]',
    )
    envfile.write_text(
        "\n".join(k + "='" + v + "'" for k, v in variables.items()) + "\n"
    )
    envfile.chmod(0o600)
    ENV.update(variables)
    ENV["STAGING_ENV_FILE"] = str(envfile)
    config = json.loads(
        run(
            DOCKER
            + [
                "compose",
                "--env-file",
                str(envfile),
                "-f",
                str(ROOT / "infra/compose.staging.yaml"),
                "--profile",
                "tools",
                "config",
                "--format",
                "json",
            ]
        ).stdout
    )
    config["name"] = project
    config["services"]["proxy"]["environment"]["STAGING_GATE_HASH"] = gate_hash.replace(
        "$", "$$"
    )
    for service in config["services"].values():
        service.pop("build", None)
        service["pull_policy"] = "never"
    config["services"]["proxy"]["ports"] = [
        {
            "target": 18443,
            "published": "18443",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]
    caddy = directory / "Caddyfile"
    caddy.write_text(
        "{\n skip_install_trust\n auto_https disable_redirects\n}\n"
        + (ROOT / "infra/Caddyfile.staging").read_text()
    )
    caddy.chmod(0o644)
    config["services"]["proxy"]["volumes"][0]["source"] = str(caddy)
    config["networks"]["web"]["ipam"]["config"] = [{"subnet": "172.30.242.0/24"}]
    config["services"]["proxy"]["networks"]["web"]["ipv4_address"] = "172.30.242.10"
    config["services"]["api"]["command"] = [
        str(v).replace("172.30.241.10", "172.30.242.10")
        for v in config["services"]["api"]["command"]
    ]
    for name in ("api", "worker", "migrate"):
        # Mock routes reserve their full worst-case output; use fixture quotas.
        # Production defaults remain unchanged and are tested separately.
        config["services"][name]["environment"].update(
            QUOTA_DAILY_TOKENS="2000000",
            QUOTA_DAILY_INPUT_CHARS="2000000",
            QUOTA_DAILY_REQUESTS="2000",
        )
        config["services"][name]["volumes"].append(
            {"type": "bind", "source": str(mail), "target": "/app/.local/mail"}
        )
    cfg = directory / "compose.json"
    cfg.write_text(json.dumps(config))
    cfg.chmod(0o600)
    compose = DOCKER + [
        "compose",
        "--env-file",
        str(envfile),
        "-f",
        str(cfg),
        "-p",
        project,
    ]
    original = live_state()
    report = {"run": stamp, "paid_calls": False, "public_certificate_tested": False}
    backup = Path("/workspace/knigoskop-staging-backups") / stamp
    target_compose = None
    try:
        run(compose + ["up", "-d", "postgres", "redis"])
        run(compose + ["run", "--rm", "migrate"])
        for module, command in [
            ("app.domains.summaries.cli", "seed-summary-mock"),
            ("app.domains.audio.cli", "seed-audio-mock"),
        ]:
            run(compose + ["run", "--rm", "migrate", "python", "-m", module, command])
        run(compose + ["up", "-d", "api", "worker", "proxy"])
        proxy_id = run(compose + ["ps", "-q", "proxy"]).stdout.strip()
        cert = directory / "root.crt"
        for attempt in range(60):
            r = run(
                DOCKER
                + [
                    "cp",
                    proxy_id + ":/data/caddy/pki/authorities/local/root.crt",
                    str(cert),
                ],
                check=False,
            )
            if r.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Local CA not ready")
        url = "https://localhost:18443"
        with httpx.Client(
            base_url=url, verify=str(cert), trust_env=False, timeout=20
        ) as client:
            assert client.get("/").status_code == 401
            client.auth = ("reviewer", gate_password)
            response = client.get("/")
            assert (
                response.status_code == 200
                and "/assets/" in response.text
                and "@vite/client" not in response.text
            )
            assert response.headers["x-content-type-options"] == "nosniff"
            assert (
                "frame-ancestors 'none'" in response.headers["content-security-policy"]
            )
            ready = client.get("/ready")
            if ready.status_code != 200 or "application/json" not in ready.headers.get(
                "content-type", ""
            ):
                (directory / "ready-failure.txt").write_text(
                    str(ready.status_code) + "\n" + ready.text
                )
                raise RuntimeError("HTTPS readiness failed; private response retained")
            assert ready.json()["status"] == "ready"
            report["https_verified_with_local_ca"] = True
            report["static_assets"] = True
            report["basic_auth_gate"] = True
            assert client.get("/api/v1/admin/users").status_code == 401
            assert client.get("/api/v1/auth/csrf").status_code == 200
            assert "Secure" in client.get("/api/v1/auth/csrf").headers["set-cookie"]

            def mutate(method, path, **kw):
                return client.request(
                    method,
                    path,
                    headers={
                        "Origin": url,
                        "X-CSRF-Token": client.cookies.get("knigoskop_csrf"),
                    },
                    **kw,
                )

            password = secrets.token_urlsafe(20)
            email = "staging-test@example.com"
            assert (
                mutate(
                    "POST",
                    "/api/v1/auth/register",
                    json={"email": email, "password": password},
                ).status_code
                == 202
            )
            messages = [json.loads(p.read_text()) for p in mail.glob("*.json")]
            token = parse_qs(
                urlsplit(
                    next(m["link"] for m in messages if m["recipient"] == email)
                ).fragment
            )["token"][0]
            assert (
                mutate(
                    "POST", "/api/v1/auth/verify-email", json={"token": token}
                ).status_code
                == 200
            )
            assert (
                mutate(
                    "POST",
                    "/api/v1/auth/login",
                    json={"email": email, "password": password},
                ).status_code
                == 200
            )
            assert (
                mutate(
                    "POST",
                    "/api/v1/books/upload",
                    data={"title": "Staging fixture", "author": "Fixture"},
                    files={
                        "file": (
                            "fixture.txt",
                            "Читатель открыл книгу. Он отметил абзац. ".encode() * 10,
                        )
                    },
                ).status_code
                == 201
            )
            item = client.get("/api/v1/library").json()["items"][0]
            response = mutate(
                "POST",
                f"/api/v1/library/{item['id']}/process",
                json={"idempotency_key": secrets.token_hex(16)},
            )
            if response.status_code != 202:
                (directory / "job-failure.txt").write_text(
                    str(response.status_code) + "\n" + response.text
                )
                raise RuntimeError("Summary job rejected; private response retained")
            job = response.json()
            for attempt in range(120):
                status = client.get("/api/v1/jobs/" + job["id"]).json()
                if status["status"] in ("completed", "failed"):
                    break
                time.sleep(0.25)
            assert status["status"] == "completed"
            assert client.get("/api/v1/admin/audit").status_code == 403
            report["registration_login_import_mock_summary"] = True
        # Preserve role, ciphertext and files in a quiescent maintenance backup.
        run(compose + ["stop", "api", "worker"])
        command = [
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "scripts/ops/backup_restore.py"),
            "backup",
            "--env",
            str(envfile),
            "--compose",
            str(cfg),
            "--project",
            project,
            "--output",
            str(backup),
            "--local-socket",
        ]
        run(command)
        report["backup_verified"] = True
        targetcfg = json.loads(json.dumps(config))
        targetcfg["name"] = target
        targetcfg["services"]["api"]["networks"] = {"backend": None}
        # Restore only postgres/redis/API definition; leave proxy and worker stopped.
        for service in targetcfg["services"].values():
            service.pop("depends_on", None)
        for volume in targetcfg.get("volumes", {}).values():
            volume.pop("name", None)
        for network in targetcfg.get("networks", {}).values():
            network.pop("name", None)
        restorecfg = directory / "restore.json"
        restorecfg.write_text(json.dumps(targetcfg))
        restorecfg.chmod(0o600)
        target_compose = DOCKER + [
            "compose",
            "--env-file",
            str(envfile),
            "-f",
            str(restorecfg),
            "-p",
            target,
        ]
        run(target_compose + ["up", "-d", "postgres", "redis"])
        run(target_compose + ["create", "api"])
        command[2] = "restore"
        del command[command.index("--output") : command.index("--output") + 2]
        command[command.index("--project") + 1] = target
        command[command.index("--compose") + 1] = str(restorecfg)
        command += ["--backup", str(backup)]
        run(command)
        counts = (
            run(
                target_compose
                + [
                    "exec",
                    "-T",
                    "postgres",
                    "sh",
                    "-c",
                    'psql -U "$APP_POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM users; SELECT count(*) FROM summaries; SELECT version_num FROM alembic_version;"',
                ]
            )
            .stdout.strip()
            .splitlines()
        )
        assert counts[0] == "1" and int(counts[1]) == 3 and counts[2] == "0009_admin"
        report["restore_fresh_db_app_role"] = True
        # Verify stored original/normalized files after restore without exposing content.
        helper = run(
            target_compose
            + [
                "run",
                "--rm",
                "--no-deps",
                "api",
                "python",
                "-c",
                "import hashlib; from sqlalchemy import select; from sqlalchemy.orm import Session; from app.db.session import get_engine; from app.db.models import TextVersion; from app.integrations.storage import get_storage; s=Session(get_engine()); rows=s.scalars(select(TextVersion)).all(); assert len(rows)==1; st=get_storage(); assert all(hashlib.sha256(st.read(r.storage_key)).hexdigest()==r.original_checksum and hashlib.sha256(st.read(r.normalized_text_key)).hexdigest()==r.checksum for r in rows); print('files_verified')",
            ]
        )
        assert "files_verified" in helper.stdout
        report["restored_file_checksums"] = True
        report["original_containers_unchanged"] = live_state() == original
        assert report["original_containers_unchanged"]
        report["passed"] = True
    finally:
        if not report.get("passed"):
            for service in ("postgres", "api", "proxy", "worker"):
                result = run(compose + ["logs", "--tail=80", service], check=False)
                path = directory / (service + ".log")
                path.write_text(result.stdout + "\n" + result.stderr)
                path.chmod(0o600)
        for cmd in [target_compose, compose]:
            if cmd:
                run(cmd + ["down", "--remove-orphans"], check=False)
                label_project = target if cmd is target_compose else project
                volumes = run(
                    DOCKER
                    + [
                        "volume",
                        "ls",
                        "--filter",
                        "label=com.docker.compose.project=" + label_project,
                        "-q",
                    ]
                ).stdout.splitlines()
                if volumes:
                    run(DOCKER + ["volume", "rm", *volumes], check=False)
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))


if __name__ == "__main__":
    main()
