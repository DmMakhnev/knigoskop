"""Bounded, pinned HTTPS transport and explicitly compatible structured LLMs."""

import http.client
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.errors import AppError
from app.core.provider_secrets import resolve_key
from app.integrations import sources

DIAGNOSTIC_TEXT = "Return the JSON object with ok equal to true."
DIAGNOSTIC_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class ProviderFailure(Exception):
    def __init__(
        self, code, *, technical=False, safe_retry=False, units=None, reason=None
    ):
        # Only controlled codes, never upstream bodies/headers/errors.
        super().__init__(code)
        self.code = code
        self.technical = technical
        self.safe_retry = safe_retry
        self.units = units or {}
        self.reason = reason


@dataclass
class Generation:
    content: dict
    units: dict


class LLMProvider(Protocol):
    def generate_structured(
        self, text: str, schema: dict, *, max_tokens: int, idempotency_key: str
    ) -> Generation: ...


def validate_destination(url, host):
    try:
        parsed = sources.destination(url, host)
        if host not in settings.ai_allowed_hosts or parsed.query:
            raise ValueError()
        return parsed
    except (AppError, ValueError):
        raise AppError(
            422, "provider_url_blocked", "Адрес AI-провайдера не разрешён"
        ) from None


def llm_connection(host, address, timeout):
    """Use an operator-configured cloud CONNECT route, without TLS bypasses.

    Destination validation and public DNS checks occur before this helper.
    The trusted egress proxy resolves the exact allowlisted hostname itself;
    direct deployments retain IP pinning. Proxy credentials are not extracted.
    """
    if not settings.ai_https_proxy or not settings.ai_https_proxy.get_secret_value():
        return sources.PinnedHTTPS(host, address, timeout)
    try:
        proxy = urlsplit(settings.ai_https_proxy.get_secret_value())
        if (
            proxy.scheme != "http"
            or not proxy.hostname
            or proxy.username is not None
            or proxy.password is not None
            or proxy.path not in ("", "/")
            or proxy.query
            or proxy.fragment
        ):
            raise ValueError()
        context = ssl.create_default_context(cafile=settings.ai_ca_bundle)
        connection = http.client.HTTPSConnection(
            proxy.hostname, proxy.port or 80, timeout=timeout, context=context
        )
        connection.set_tunnel(host, 443)
        return connection
    except (ValueError, OSError):
        raise ProviderFailure("provider_proxy_configuration") from None


def post_json(url, host, key, payload, idempotency_key):
    try:
        parsed = validate_destination(url, host)
    except AppError:
        raise ProviderFailure("provider_url_blocked") from None
    if not sources.slots.acquire(blocking=False):
        raise ProviderFailure("provider_busy", technical=True, safe_retry=True)
    connection = None
    sent = False
    try:
        deadline = time.monotonic() + settings.ai_timeout_seconds
        addresses = sources.resolve(host)
        if not addresses or any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise ProviderFailure("provider_url_blocked")
        connection = llm_connection(
            host, addresses[0][4][0], max(0.001, deadline - time.monotonic())
        )
        body = json.dumps(payload).encode()
        # Once the request begins, a timeout can mean the remote call was billed.
        sent = True
        connection.request(
            "POST",
            parsed.path or "/",
            body=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
                "Idempotency-Key": idempotency_key,
            },
        )
        response = connection.getresponse()
        if response.status == 429:
            raise ProviderFailure(
                "provider_rate_limit", technical=True, safe_retry=True
            )
        if response.status in (401, 403):
            raise ProviderFailure("provider_authentication")
        if response.status >= 500:
            # Conservative: response doesn't prove no billable work occurred.
            raise ProviderFailure("provider_unavailable", technical=True)
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise ProviderFailure("provider_protocol")
        result = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderFailure("provider_timeout", technical=True)
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read1(
                min(65536, settings.ai_response_max_bytes + 1 - len(result))
            )
            if not chunk:
                return json.loads(result)
            result.extend(chunk)
            if len(result) > settings.ai_response_max_bytes:
                raise ProviderFailure("provider_protocol")
    except (socket.timeout, TimeoutError):
        raise ProviderFailure(
            "provider_timeout", technical=True, safe_retry=not sent
        ) from None
    except (OSError, http.client.HTTPException):
        raise ProviderFailure(
            "provider_unavailable", technical=True, safe_retry=not sent
        ) from None
    except (ValueError, TypeError):
        raise ProviderFailure("provider_protocol") from None
    except AppError:
        raise ProviderFailure(
            "provider_unavailable", technical=True, safe_retry=not sent
        ) from None
    finally:
        if connection:
            connection.close()
        sources.slots.release()


class MockLLM:
    def __init__(self, config):
        self.behavior = config["capabilities"].get("mock_behavior", "success")

    def generate_structured(self, text, schema, *, max_tokens, idempotency_key):
        if self.behavior in ("timeout", "rate_limit", "unavailable"):
            raise ProviderFailure(
                "provider_" + self.behavior, technical=True, safe_retry=True
            )
        if self.behavior == "authentication":
            raise ProviderFailure("provider_authentication")
        if schema != DIAGNOSTIC_SCHEMA and self.behavior != "invalid_content":
            from app.domains.summaries.mock import generate

            return Generation(
                generate(text),
                {"input_tokens": len(text.encode()), "output_tokens": 100},
            )
        return Generation(
            {"ok": self.behavior != "invalid_content"},
            {"input_tokens": 12, "output_tokens": 5},
        )


class OpenAICompatibleLLM:
    def __init__(self, config):
        self.config = config

    def generate_structured(self, text, schema, *, max_tokens, idempotency_key):
        config = self.config
        key = resolve_key(config)
        data = post_json(
            config["base_url"],
            config["host"],
            key,
            {
                "model": config["model_identifier"],
                "messages": [{"role": "user", "content": text}],
                "max_tokens": max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "knigoskop",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
            idempotency_key,
        )
        units = {}
        try:
            raw = data["usage"]
            for source, target in (
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
            ):
                count = raw[source]
                if type(count) is not int or count < 0 or count > 10000000:
                    raise ValueError()
                units[target] = count
            content = json.loads(data["choices"][0]["message"]["content"])
            if not isinstance(content, dict):
                raise ValueError()
            return Generation(content, units)
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderFailure("provider_content_invalid", units=units) from None


def get_provider(config) -> LLMProvider:
    if config["protocol"] == "mock":
        return MockLLM(config)
    if config["protocol"] == "openai_chat":
        return OpenAICompatibleLLM(config)
    raise ProviderFailure("provider_protocol")


class ProviderRouter:
    """Frozen plan with bounded retries; never fallback on ambiguous billing."""

    def __init__(self, snapshot):
        self.snapshot = snapshot

    def plan(self):
        return [
            model
            for model in self.snapshot["models"]
            for _ in range(self.snapshot["policy"]["attempts_per_model"])
        ]

    def may_retry(self, failure, attempt):
        return failure.technical and failure.safe_retry and attempt < len(self.plan())
