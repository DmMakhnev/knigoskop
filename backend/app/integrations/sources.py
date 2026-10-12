"""Explicitly reviewed HTTPS JSON catalogs; no scraping or automatic rights grants."""

import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.errors import AppError
from app.domains.library.schemas import Metadata


class Candidate(Metadata):
    id: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2000)
    format: Literal["txt", "pdf", "epub", "docx", "fb2"]


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    rights_status: Literal["cc0", "public_domain", "licensed"]
    provenance: str = Field(min_length=1, max_length=500)


def fingerprint(candidate):
    return hashlib.sha256(candidate.model_dump_json().encode()).hexdigest()


def unavailable():
    return AppError(503, "source_unavailable", "Источник временно недоступен")


def destination(url, host):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or not host
    ):
        raise AppError(422, "source_url_blocked", "Адрес источника не разрешён")
    return parsed


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the validated numeric IP; TLS still verifies hostname and SNI.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


slots = threading.BoundedSemaphore(2)
dns_slots = threading.BoundedSemaphore(2)
dns_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="source-dns")


def resolve(host):
    if not dns_slots.acquire(blocking=False):
        raise unavailable()
    future = dns_pool.submit(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    future.add_done_callback(lambda result: dns_slots.release())
    try:
        return future.result(timeout=settings.source_timeout_seconds)
    except TimeoutError:
        raise unavailable() from None


def fetch_https(url: str, host: str, max_bytes: int) -> bytes:
    parsed = destination(url, host)
    if not slots.acquire(blocking=False):
        raise unavailable()
    connection = None
    try:
        deadline = time.monotonic() + settings.source_timeout_seconds
        addresses = resolve(host)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise AppError(422, "source_url_blocked", "Адрес источника не разрешён")
        connection = PinnedHTTPS(
            host, addresses[0][4][0], max(0.001, deadline - time.monotonic())
        )
        connection.request(
            "GET",
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            headers={"Accept-Encoding": "identity", "User-Agent": "Knigoskop/0.1"},
        )
        response = connection.getresponse()
        # Redirects are refused, including redirects within the allowed host.
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise unavailable()
        result = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise unavailable()
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - len(result)))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
            if len(result) > max_bytes:
                raise AppError(
                    413, "file_too_large", "Источник превышает допустимый размер"
                )
    except (OSError, ValueError, http.client.HTTPException):
        raise unavailable() from None
    finally:
        if connection:
            connection.close()
        slots.release()


class SourceConnector(Protocol):
    def search(self, query: str) -> list[Candidate]: ...
    def fetch(self, candidate: Candidate) -> bytes: ...


class JsonCatalogConnector:
    def __init__(self, source):
        self.source = source

    def search(self, query):
        data = fetch_https(
            self.source.catalog_url, self.source.host, settings.source_catalog_max_bytes
        )
        raw = json.loads(data)
        if not isinstance(raw, list) or len(raw) > settings.source_catalog_max_entries:
            raise unavailable()
        entries = [Candidate.model_validate(item) for item in raw]
        if len({entry.id for entry in entries}) != len(entries):
            raise unavailable()
        words = query.casefold().split()
        return [
            entry
            for entry in entries
            if all(
                word
                in (
                    entry.title
                    + " "
                    + entry.author
                    + " "
                    + entry.edition_label
                    + " "
                    + entry.translator
                ).casefold()
                for word in words
            )
        ]

    def fetch(self, candidate):
        return fetch_https(candidate.url, self.source.host, settings.upload_max_bytes)


def get_connector(source) -> SourceConnector:
    return JsonCatalogConnector(source)
