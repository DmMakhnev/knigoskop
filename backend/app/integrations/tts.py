"""Native GenVoice binary transport; demo signals are never presented as speech."""

import http.client
import io
import ipaddress
import json
import re
import socket
import time
import wave
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from app.core.config import settings
from app.core.errors import AppError
from app.core.provider_secrets import decrypt
from app.integrations import sources
from app.integrations.llm import ProviderFailure, llm_connection, validate_destination


@dataclass
class Speech:
    audio: bytes
    marks: list
    alignment: str


class TTSProvider(Protocol):
    def synthesize(
        self, text: str, voice: str, key: str, *, language: str = "ru"
    ) -> Speech: ...


def credential(profile):
    if profile["protocol"] == "mock":
        return ""
    if profile.get("encrypted_secret"):
        return decrypt(profile["encrypted_secret"])
    yandex = profile["protocol"] == "yandex_speechkit"
    secret = (
        settings.knigoskop_yandex_tts_api_key
        if yandex
        else settings.knigoskop_tts_api_key
    )
    expected_host = (
        settings.yandex_tts_env_provider_host
        if yandex
        else settings.tts_env_provider_host
    )
    value = secret.get_secret_value() if secret else ""
    if (
        profile["host"] != expected_host
        or not value
        or len(value) > 2000
        or any(c in value for c in "\r\n")
    ):
        raise AppError(503, "tts_unavailable", "Ключ озвучивания не настроен")
    return value


class MockTTS:
    def synthesize(self, text, voice, key, *, language="ru"):
        output = io.BytesIO()
        marks = []
        with wave.open(output, "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            for index, word in enumerate(re.finditer(r"\S+", text)):
                # Exact, known 200 ms test signals; not generated speech.
                audio.writeframes((b"\x00\x08" * 40 + b"\x00\xf8" * 40) * 20)
                marks.append(
                    {
                        "start_ms": index * 200,
                        "end_ms": (index + 1) * 200,
                        "text_start": word.start(),
                        "text_end": word.end(),
                    }
                )
        return Speech(output.getvalue(), marks, "words")


class GenVoice:
    path = "/api/tts"
    authorization = "Bearer "
    content_type = "application/json"
    response_types = ("audio/wav", "audio/x-wav", "application/octet-stream")

    def payload(self, text, voice, language):
        return json.dumps(
            {"text": text, "voice_id": voice, "output_format": "wav_24000"}
        ).encode()

    def audio_bytes(self, data):
        return data

    def __init__(self, profile):
        self.profile = profile

    def synthesize(self, text, voice, key, *, language="ru"):
        url = self.profile["base_url"].rstrip("/") + self.path
        try:
            parsed = validate_destination(url, self.profile["host"])
        except AppError:
            raise ProviderFailure("provider_url_blocked") from None
        if not sources.slots.acquire(blocking=False):
            raise ProviderFailure("provider_busy", technical=True, safe_retry=True)
        connection = None
        sent = False
        try:
            deadline = time.monotonic() + settings.ai_timeout_seconds
            addresses = sources.resolve(self.profile["host"])
            if not addresses or any(
                not ipaddress.ip_address(a[4][0]).is_global for a in addresses
            ):
                raise ProviderFailure("provider_url_blocked")
            connection = llm_connection(
                self.profile["host"], addresses[0][4][0], settings.ai_timeout_seconds
            )
            payload = self.payload(text, voice, language)
            sent = True
            connection.request(
                "POST",
                parsed.path,
                body=payload,
                headers={
                    "Authorization": self.authorization + key,
                    "Content-Type": self.content_type,
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in (401, 403):
                raise ProviderFailure("provider_authentication")
            if response.status == 429:
                raise ProviderFailure(
                    "provider_rate_limit", technical=True, safe_retry=True
                )
            if response.status >= 500:
                raise ProviderFailure("provider_outcome_unknown", technical=True)
            if (
                response.status != 200
                or response.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise ProviderFailure("provider_protocol")
            content_type = response.getheader("Content-Type", "").split(";")[0].lower()
            if content_type not in self.response_types:
                raise ProviderFailure("audio_content_invalid")
            data = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderFailure("provider_outcome_unknown", technical=True)
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(
                    min(65536, settings.audio_max_bytes + 1 - len(data))
                )
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > settings.audio_max_bytes:
                    raise ProviderFailure("audio_size_limit")
            # Native TTS supplies no word marks; do not manufacture alignment.
            return Speech(self.audio_bytes(bytes(data)), [], "none")
        except (socket.timeout, TimeoutError, OSError, http.client.HTTPException):
            raise ProviderFailure(
                "provider_outcome_unknown" if sent else "provider_unavailable",
                technical=True,
                safe_retry=not sent,
            ) from None
        except AppError:
            raise ProviderFailure(
                "provider_unavailable", technical=True, safe_retry=not sent
            ) from None
        finally:
            if connection:
                connection.close()
            sources.slots.release()


class YandexSpeechKit(GenVoice):
    """SpeechKit v1 service-account API key; PCM is wrapped in a WAV container."""

    path = "/speech/v1/tts:synthesize"
    authorization = "Api-Key "
    content_type = "application/x-www-form-urlencoded"
    response_types = (
        "audio/x-pcm",
        "audio/pcm",
        "audio/lpcm",
        "application/octet-stream",
    )

    def payload(self, text, voice, language):
        languages = {"ru": "ru-RU", "en": "en-US", "kk": "kk-KK"}
        return urlencode(
            {
                "text": text,
                "voice": voice,
                "lang": languages.get(language, language),
                "format": "lpcm",
                "sampleRateHertz": "16000",
                "speed": "1.0",
            }
        ).encode()

    def audio_bytes(self, data):
        if not data or len(data) % 2:
            raise ProviderFailure("audio_content_invalid")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(data)
        return output.getvalue()


def get_provider(profile):
    if profile["protocol"] == "mock":
        return MockTTS()
    if profile["protocol"] == "genvoice":
        return GenVoice(profile)
    if profile["protocol"] == "yandex_speechkit":
        return YandexSpeechKit(profile)
    raise ProviderFailure("tts_protocol_unsupported")


def genvoice_public_voices(profile):
    """Read-only public voice catalog; no synthesis and no credit-spending call."""
    key = credential(profile)
    parsed = validate_destination(
        profile["base_url"].rstrip("/") + "/api/voices/public", profile["host"]
    )
    if not sources.slots.acquire(blocking=False):
        raise AppError(503, "provider_busy", "Сервис временно занят")
    connection = None
    try:
        addresses = sources.resolve(profile["host"])
        if not addresses or any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise AppError(422, "provider_url_blocked", "Адрес провайдера не разрешён")
        connection = llm_connection(
            profile["host"], addresses[0][4][0], settings.ai_timeout_seconds
        )
        connection.request(
            "GET",
            parsed.path,
            headers={"Authorization": "Bearer " + key, "Accept-Encoding": "identity"},
        )
        response = connection.getresponse()
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise AppError(503, "tts_catalog_unavailable", "Список голосов недоступен")
        deadline = time.monotonic() + settings.ai_timeout_seconds
        chunks = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError()
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(8192, 65537 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 65536:
                raise ValueError()
        data = b"".join(chunks)
        if len(data) > 65536:
            raise ValueError()
        records = json.loads(data)
        if not isinstance(records, list) or len(records) > 500:
            raise ValueError()
        from uuid import UUID

        voices = []
        for record in records:
            identifier = str(UUID(record["id"]))
            voices.append(
                {"id": identifier, "name": str(record.get("name", identifier))[:100]}
            )
        return voices
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        http.client.HTTPException,
        ProviderFailure,
    ):
        raise AppError(
            503, "tts_catalog_unavailable", "Не удалось получить список голосов"
        ) from None
    finally:
        if connection:
            connection.close()
        sources.slots.release()
