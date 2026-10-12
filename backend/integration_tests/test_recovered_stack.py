import io
import subprocess
import time
import zipfile
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db.ai_models import UsageEvent
from app.db.models import Base
from app.db.session import get_engine
from conftest import completed_job, upload


def test_migrations_apply_to_real_postgres_and_are_repeatable():
    subprocess.run(["alembic", "upgrade", "head"], check=True, capture_output=True)
    engine = get_engine()
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0009_admin"
        )
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    assert inspect(engine).get_foreign_keys("library_items")
    assert inspect(engine).get_check_constraints("summary_source_spans")


def test_frontend_serves_restored_source_and_proxies_api():
    with httpx.Client(
        base_url="http://frontend:5173",
        trust_env=False,
        timeout=5,
        headers={"Host": "localhost"},
    ) as client:
        deadline = time.monotonic() + 20
        while True:
            try:
                response = client.get("/")
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("Frontend failed to start")
            time.sleep(0.2)
        assert '<div id="root"></div>' in response.text
        assert client.get("/src/main.tsx").status_code == 200
        assert client.get("/health").json() == {
            "status": "ok",
            "service": "knigoskop-api",
        }
        assert client.get("/api/v1/me").status_code == 401


def test_email_verification_is_one_time_and_logout_revokes_session(account_factory):
    account = account_factory(verified=False)
    assert (
        account.request(
            "POST",
            "/api/v1/auth/login",
            json={"email": account.email, "password": account.password},
        ).status_code
        == 403
    )
    token = account.token("verify_email")
    assert (
        account.request(
            "POST", "/api/v1/auth/verify-email", json={"token": token}
        ).status_code
        == 200
    )
    assert (
        account.request(
            "POST", "/api/v1/auth/verify-email", json={"token": token}
        ).status_code
        == 400
    )
    account.login()
    assert account.request("GET", "/api/v1/me").status_code == 200
    assert account.request("POST", "/api/v1/auth/logout").status_code == 204
    assert account.request("GET", "/api/v1/me").status_code == 401


def test_password_reset_invalidates_old_session_and_token(account):
    assert (
        account.request(
            "POST", "/api/v1/auth/request-password-reset", json={"email": account.email}
        ).status_code
        == 202
    )
    token = account.token("password_reset")
    account.password = "New-validation-fixture-pass-2026"
    assert (
        account.request(
            "POST",
            "/api/v1/auth/reset-password",
            json={"token": token, "password": account.password},
        ).status_code
        == 200
    )
    assert account.request("GET", "/api/v1/me").status_code == 401
    account.login()
    assert (
        account.request(
            "POST",
            "/api/v1/auth/reset-password",
            json={"token": token, "password": account.password},
        ).status_code
        == 400
    )


def test_admin_data_and_blocked_user_sessions_are_protected(account_factory):
    ordinary = account_factory()
    admin = account_factory(admin=True)
    assert ordinary.request("GET", "/api/v1/admin/providers").status_code == 403
    assert admin.request("GET", "/api/v1/admin/providers").status_code == 200
    assert (
        admin.request(
            "PATCH",
            f"/api/v1/admin/users/{ordinary.user_id}",
            json={"status": "blocked"},
        ).status_code
        == 200
    )
    assert ordinary.request("GET", "/api/v1/me").status_code in (401, 403)


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def pdf_fixture():
    stream = b"BT /F1 12 Tf 50 750 Td (Hello fixture book.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return (
        result
        + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )


FORMATS = {
    "txt": b"Hello fixture book.",
    "pdf": pdf_fixture(),
    "fb2": (
        b'<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        b"<body><section><p>Hello fixture book.</p></section></body></FictionBook>"
    ),
    "docx": zipped(
        {
            "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body><w:p><w:r>'
                "<w:t>Hello fixture book.</w:t></w:r></w:p></w:body></w:document>"
            ),
        }
    ),
    "epub": zipped(
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": (
                '<container><rootfiles><rootfile full-path="book.opf"/>'
                "</rootfiles></container>"
            ),
            "book.opf": (
                '<package><manifest><item id="one" href="chapter.xhtml" '
                'media-type="application/xhtml+xml"/></manifest>'
                '<spine><itemref idref="one"/></spine></package>'
            ),
            "chapter.xhtml": (
                "<html><body><p>Hello fixture book.</p>"
                "<script>malicious</script></body></html>"
            ),
        }
    ),
}


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_import_five_formats_through_real_http_and_read_back(account, fmt):
    item = upload(account, FORMATS[fmt], fmt)
    manifest = account.request("GET", f"/api/v1/library/{item['id']}/reader").json()
    response = account.request(
        "GET",
        f"/api/v1/texts/{manifest['text_version_id']}/sections/{manifest['first_section_id']}",
    )
    assert response.status_code == 200
    assert "Hello fixture book." in response.json()["text"]
    assert "malicious" not in response.json()["text"]


def test_private_book_reader_and_progress_are_isolated(account_factory):
    owner, other = account_factory(), account_factory()
    item = upload(owner)
    manifest = owner.request("GET", f"/api/v1/library/{item['id']}/reader").json()
    version, section = manifest["text_version_id"], manifest["first_section_id"]
    for path in (
        f"/api/v1/library/{item['id']}",
        f"/api/v1/texts/{version}/sections/{section}",
        f"/api/v1/library/{item['id']}/summaries",
    ):
        assert other.request("GET", path).status_code == 404
    assert (
        other.request(
            "POST", "/api/v1/library", json={"text_version_id": version}
        ).status_code
        == 404
    )
    payload = {
        "library_item_id": item["id"],
        "text_version_id": version,
        "section_id": section,
        "offset": 1,
        "revision": 0,
    }
    saved = owner.request("PUT", "/api/v1/me/reading-progress", json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert (
        owner.request("PUT", "/api/v1/me/reading-progress", json=payload).status_code
        == 409
    )
    assert (
        owner.request("GET", f"/api/v1/library/{item['id']}/reading-progress").json()[
            "position"
        ]["offset"]
        == 1
    )


def test_shared_demo_is_deduplicated_and_deleting_link_keeps_other_reader(
    account_factory,
):
    first, second = account_factory(), account_factory()
    version = first.request("GET", "/api/v1/books/public").json()["items"][0][
        "text_version_id"
    ]
    one = first.request("POST", "/api/v1/library", json={"text_version_id": version})
    two = second.request("POST", "/api/v1/library", json={"text_version_id": version})
    assert one.status_code == two.status_code == 201
    assert one.json()["text_version_id"] == two.json()["text_version_id"]
    assert (
        first.request("DELETE", f"/api/v1/library/{one.json()['id']}").status_code
        == 204
    )
    assert (
        second.request("GET", f"/api/v1/library/{two.json()['id']}/reader").status_code
        == 200
    )


def test_mock_worker_generates_three_levels_with_valid_spans_and_provenance(
    account, account_factory
):
    item = upload(account)
    item_id = item["id"]
    payload = {"idempotency_key": "summary-" + uuid4().hex}
    response = account.request(
        "POST", f"/api/v1/library/{item_id}/process", json=payload
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert (
        account.request(
            "POST", f"/api/v1/library/{item_id}/process", json=payload
        ).json()["id"]
        == job_id
    )
    completed_job(account, job_id)
    manifest = account.request("GET", f"/api/v1/library/{item_id}/reader").json()
    for level in ("short", "medium", "detailed"):
        summaries = account.request(
            "GET", f"/api/v1/library/{item_id}/summaries", params={"level": level}
        ).json()["items"]
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary["provenance"]
        nodes = account.request(
            "GET", f"/api/v1/library/{item_id}/summaries/{summary['id']}/nodes"
        ).json()["items"]
        assert nodes and any(n["text"] for n in nodes)
        for node in nodes:
            for span in node["source_spans"]:
                assert span["text_version_id"] == item["text_version_id"]
                assert (
                    0 <= span["start_offset"] < span["end_offset"] <= manifest["length"]
                )
        validations = account.request(
            "GET", f"/api/v1/library/{item_id}/summaries/{summary['id']}/validations"
        ).json()["items"]
        assert validations and all(v["provenance"] for v in validations)
    other = account_factory()
    assert other.request("GET", f"/api/v1/jobs/{job_id}").status_code == 404
    with Session(get_engine()) as db:
        usage = db.scalars(select(UsageEvent).where(UsageEvent.job_id == job_id)).all()
        assert usage and all(row.status == "completed" for row in usage)


def test_zip_traversal_is_rejected_without_library_entry(account):
    before = account.request("GET", "/api/v1/library").json()["total"]
    response = account.request(
        "POST",
        "/api/v1/books/upload",
        data={"title": "Unsafe fixture"},
        files={"file": ("bad.docx", zipped({"../escape": "fixture"}))},
    )
    assert response.status_code == 422
    assert account.request("GET", "/api/v1/library").json()["total"] == before
