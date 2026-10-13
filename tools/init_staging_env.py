"""Create a private first-install staging configuration; never overwrite or print keys."""

import argparse
import base64
import getpass
import json
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]


def create(domain, gate_hash, path):
    if (
        not re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain
        )
        or domain.endswith(".example.com")
        or domain == "example.com"
    ):
        raise ValueError("Use your real DNS hostname without scheme, port or path")
    if not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", gate_hash):
        raise ValueError("Valid bcrypt gate hash required")
    values = {}
    for line in (ROOT / "infra/staging.env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            values[k] = v
    values.update(
        STAGING_DOMAIN=domain,
        PUBLIC_BASE_URL="https://" + domain,
        ALLOWED_ORIGINS=json.dumps(["https://" + domain]),
        STAGING_GATE_HASH=gate_hash,
        POSTGRES_ADMIN_PASSWORD=secrets.token_urlsafe(32),
        APP_POSTGRES_PASSWORD=secrets.token_urlsafe(32),
        AI_MASTER_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    )
    with path.open("x") as f:
        path.chmod(0o600)
        f.write("\n".join(k + "='" + v + "'" for k, v in values.items()) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--domain", required=True)
    p.add_argument("--output", type=Path, default=ROOT / ".env.staging")
    a = p.parse_args()
    if a.output.exists():
        p.exit(1, "Configuration already exists; no changes made.\n")
    try:
        create(
            a.domain,
            getpass.getpass(
                "Вставьте bcrypt-хеш пароля входа на staging (не отображается): "
            ),
            a.output,
        )
    except (ValueError, OSError):
        p.exit(
            1,
            "Configuration not created: check domain, bcrypt hash, path and permissions.\n",
        )
    print(
        "Private staging configuration created. Keys were not printed; registration disabled."
    )


if __name__ == "__main__":
    main()
