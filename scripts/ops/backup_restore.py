"""Private maintenance backup and restore into an empty separate Docker project."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, UTC

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Operations:
    def __init__(self, args):
        self.args = args
        self.env = os.environ.copy()
        self.env["STAGING_ENV_FILE"] = str(Path(args.env).resolve())
        for name in [
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        ]:
            self.env.pop(name, None)
        self.docker = (
            ["docker", "--host=unix:///var/run/docker.sock"]
            if args.local_socket
            else ["docker"]
        )
        self.compose = self.docker + [
            "compose",
            "--env-file",
            str(Path(args.env).resolve()),
            "-f",
            str(Path(args.compose).resolve()),
            "-p",
            args.project,
        ]

    def run(self, command, output=None, input_file=None):
        with output.open("xb") if output else open(os.devnull, "wb") as out:
            if output:
                output.chmod(0o600)
            with input_file.open("rb") if input_file else open(os.devnull, "rb") as src:
                result = subprocess.run(
                    command,
                    env=self.env,
                    stdin=src,
                    stdout=out if output else subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
        if result.returncode:
            raise RuntimeError(
                "Operation failed: "
                + command[0]
                + " (details withheld to protect data)"
            )
        return result.stdout.decode().strip() if not output else None

    def stopped(self):
        ids = self.run(self.compose + ["ps", "-a", "-q", "api", "worker"]).splitlines()
        for identifier in ids:
            if (
                self.run(
                    self.docker
                    + ["inspect", "--format", "{{.State.Running}}", identifier]
                )
                == "true"
            ):
                raise RuntimeError(
                    "Stop API and worker explicitly before maintenance backup/restore"
                )

    def files_volume(self):
        ids = self.run(self.compose + ["ps", "-a", "-q", "api"]).splitlines()
        if len(ids) != 1:
            raise RuntimeError(
                "Exactly one API container must exist; create it without starting if restoring"
            )
        mounts = json.loads(
            self.run(self.docker + ["inspect", "--format", "{{json .Mounts}}", ids[0]])
        )
        matches = [
            m
            for m in mounts
            if m.get("Destination") == "/app/.local/files" and m.get("Type") == "volume"
        ]
        if len(matches) != 1:
            raise RuntimeError("Named files volume not found")
        return matches[0]["Name"]

    def helper(self, volume, command, output=None, input_file=None, readonly=True):
        # Existing image only; never pull/build as part of backup or restore.
        image = self.run(self.compose + ["images", "-q", "api"])
        if not image:
            raise RuntimeError("API image must exist locally")
        cmd = (
            self.docker
            + [
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
                "--user",
                "0",
                "--mount",
                f"type=volume,src={volume},dst=/files"
                + (",readonly" if readonly else ""),
                "-i",
                image,
            ]
            + command
        )
        return self.run(cmd, output, input_file)


def verified(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    for record in manifest["files"]:
        name = record["name"]
        if name not in ("database.dump", "roles.sql", "files.tar", "environment.env"):
            raise RuntimeError("Unknown backup member")
        p = directory / name
        if (
            p.is_symlink()
            or p.stat().st_size != record["bytes"]
            or sha(p) != record["sha256"]
        ):
            raise RuntimeError("Backup checksum mismatch")
    if {r["name"] for r in manifest["files"]} != {
        "database.dump",
        "roles.sql",
        "files.tar",
        "environment.env",
    }:
        raise RuntimeError("Incomplete backup")
    with tarfile.open(directory / "files.tar") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if member.isdir() and name in ("", "."):
                continue
            if not member.isfile() or not re.fullmatch("[a-f0-9]{32}", name):
                raise RuntimeError("Unsafe file archive")
            if archive.extractfile(member).read(1) is None:
                raise RuntimeError("Unreadable archive")
    return manifest


def backup(op, args):
    output = Path(args.output).resolve()
    if output == ROOT or ROOT in output.parents:
        raise RuntimeError("Sensitive backups must be outside checkout")
    op.stopped()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    op.run(
        op.compose
        + [
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'pg_dump -U postgres -d "$POSTGRES_DB" -Fc --no-owner --no-acl',
        ],
        output / "database.dump",
    )
    op.run(
        op.compose
        + ["exec", "-T", "postgres", "pg_dumpall", "-U", "postgres", "--globals-only"],
        output / "roles.sql",
    )
    op.helper(
        op.files_volume(),
        ["tar", "-C", "/files", "-cf", "-", "."],
        output / "files.tar",
    )
    shutil.copyfile(args.env, output / "environment.env")
    (output / "environment.env").chmod(0o600)
    op.run(
        op.compose + ["exec", "-T", "postgres", "pg_restore", "--list"],
        input_file=output / "database.dump",
    )
    records = [
        {
            "name": name,
            "bytes": (output / name).stat().st_size,
            "sha256": sha(output / name),
        }
        for name in ("database.dump", "roles.sql", "files.tar", "environment.env")
    ]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "project": args.project,
        "sensitive": True,
        "consistent": True,
        "consistency_method": "API/worker stopped; logical pg_dump plus stable files",
        "redis_restore": "not copied; durable jobs are in PostgreSQL",
        "roles_restore": "reference only; bootstrap role + pg_restore --role app user",
        "files": records,
    }
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    path.chmod(0o600)
    verified(output)
    return {
        "backup": str(output),
        "verified": True,
        "independent_copy_confirmed": False,
    }


def restore(op, args):
    directory = Path(args.backup).resolve()
    manifest = verified(directory)
    if (
        not args.project.startswith("knigoskop-restore-")
        or args.project == manifest["project"]
    ):
        raise RuntimeError("Restore requires a separate knigoskop-restore-* project")
    op.stopped()
    # Target must already be bootstrapped but contain no application tables or files.
    count = op.run(
        op.compose
        + [
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'psql -U postgres -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema=\x27public\x27"',
        ]
    )
    if count != "0":
        raise RuntimeError("Restore target database is not empty")
    volume = op.files_volume()
    if op.helper(volume, ["find", "/files", "-mindepth", "1", "-print", "-quit"]):
        raise RuntimeError("Restore target files volume is not empty")
    op.run(
        op.compose
        + [
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'pg_restore -U postgres -d "$POSTGRES_DB" --role="$APP_POSTGRES_USER" --no-owner --no-acl --exit-on-error',
        ],
        input_file=directory / "database.dump",
    )
    op.helper(
        volume,
        ["tar", "-C", "/files", "-xf", "-", "--no-same-owner"],
        input_file=directory / "files.tar",
        readonly=False,
    )
    # App image uses uid/gid 1000; restore service must use the same identity.
    op.helper(volume, ["chown", "-R", "1000:1000", "/files"], readonly=False)
    return {
        "restored_project": args.project,
        "source_backup": str(directory),
        "services_started": False,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["backup", "restore", "verify"])
    p.add_argument("--env", default=str(ROOT / ".env.staging"))
    p.add_argument("--compose", default=str(ROOT / "infra/compose.staging.yaml"))
    p.add_argument("--project", default="knigoskop-staging")
    p.add_argument("--output")
    p.add_argument("--backup")
    p.add_argument("--local-socket", action="store_true")
    args = p.parse_args()
    try:
        if args.action == "verify":
            if not args.backup:
                p.error("--backup required")
            verified(Path(args.backup))
            result = {"verified": True}
        elif args.action == "backup":
            if not args.output:
                p.error("--output required")
            result = backup(Operations(args), args)
        else:
            if not args.backup:
                p.error("--backup required")
            result = restore(Operations(args), args)
    except (RuntimeError, OSError, ValueError, KeyError, tarfile.TarError):
        p.exit(
            1,
            "Operation rejected/failed. Check maintenance state, paths, permissions and backup integrity; secret details withheld.\n",
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
