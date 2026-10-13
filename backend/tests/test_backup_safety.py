"""Archive validation must reject traversal even with matching manifest hashes."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "backup_module", ROOT / "scripts/ops/backup_restore.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def archive(directory, name, symlink=False):
    for filename in ("database.dump", "roles.sql", "environment.env"):
        (directory / filename).write_bytes(b"fixture")
    with tarfile.open(directory / "files.tar", "w") as output:
        item = tarfile.TarInfo(name)
        if symlink:
            item.type = tarfile.SYMTYPE
            item.linkname = "/etc/passwd"
            output.addfile(item)
        else:
            item.size = 3
            output.addfile(item, io.BytesIO(b"abc"))
    records = [
        {
            "name": f,
            "bytes": (directory / f).stat().st_size,
            "sha256": module.sha(directory / f),
        }
        for f in ("database.dump", "roles.sql", "environment.env", "files.tar")
    ]
    (directory / "manifest.json").write_text(json.dumps({"files": records}))


@pytest.mark.parametrize(
    "name,symlink", [("../escape", False), ("/absolute", False), ("a" * 32, True)]
)
def test_restore_rejects_unsafe_archives(tmp_path, name, symlink):
    archive(tmp_path, name, symlink)
    with pytest.raises(RuntimeError):
        module.verified(tmp_path)


def test_verify_detects_modified_backup_and_accepts_regular_files(tmp_path):
    archive(tmp_path, "a" * 32)
    assert module.verified(tmp_path)["files"]
    (tmp_path / "database.dump").write_bytes(b"changed")
    with pytest.raises(RuntimeError):
        module.verified(tmp_path)
