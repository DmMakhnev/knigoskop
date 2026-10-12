import io
import json

import pytest

from app.domains.audio.timings import segments, validated_marks, wav_duration
from app.integrations import tts
from app.integrations.llm import ProviderFailure


def test_genvoice_catalog_is_read_only_and_discards_extra_metadata(monkeypatch):
    observed = {}
    voice_id = "f561cd20-a52a-4551-a1e3-f51f0a45c9fd"

    class Reply:
        status = 200

        def __init__(self):
            self.payload = io.BytesIO(
                json.dumps(
                    [{"id": voice_id, "name": "Fixture", "private": "discard"}]
                ).encode()
            )

        def getheader(self, name, default=None):
            return default

        def read1(self, size):
            return self.payload.read(size)

    class Connection:
        sock = None

        def request(self, method, path, headers):
            observed.update(method=method, path=path)
            assert headers["Authorization"] == "Bearer fixture-key"

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(tts, "credential", lambda p: "fixture-key")
    monkeypatch.setattr(tts.settings, "ai_allowed_hosts", ["tts.example.com"])
    monkeypatch.setattr(
        tts.sources,
        "resolve",
        lambda host: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(tts, "llm_connection", lambda *args: Connection())
    assert tts.genvoice_public_voices(
        {"base_url": "https://tts.example.com/v1", "host": "tts.example.com"}
    ) == [{"id": voice_id, "name": "Fixture"}]
    assert observed == {"method": "GET", "path": "/v1/api/voices/public"}


def test_demo_has_exact_marks_and_wav_length():
    text = "Книга 😀 читается."
    speech = tts.MockTTS().synthesize(text, "demo", "")
    assert speech.alignment == "words"
    duration = wav_duration(speech.audio)
    assert duration == 600
    assert validated_marks(text, speech.marks, duration) == speech.marks
    assert speech.marks[1]["text_start"] == 6


@pytest.mark.parametrize(
    "text", ["A " * 400, "😀Слово" * 50, "Первый абзац.\nВторой абзац." * 10]
)
def test_segments_preserve_unicode_text_without_gaps(text):
    ranges = segments(text, 100)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert "".join(text[start:end] for start, end in ranges) == text
    assert all(0 < end - start <= 100 for start, end in ranges)
    assert all(
        previous[1] == following[0] for previous, following in zip(ranges, ranges[1:])
    )


@pytest.mark.parametrize("data", [b"not wav", b"RIFF\0\0\0\0WAVE", b""])
def test_invalid_audio_is_not_published(data):
    with pytest.raises(ProviderFailure):
        wav_duration(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("end_ms", 601),
        ("start_ms", -1),
        ("text_start", 1),
        ("end_ms", 0),
        ("start_ms", 0.0),
    ],
)
def test_bad_timings_are_rejected(field, value):
    speech = tts.MockTTS().synthesize("One", "demo", "")
    speech.marks[0][field] = value
    with pytest.raises(ProviderFailure):
        validated_marks("One", speech.marks, 200)


def test_genvoice_uses_documented_native_contract_and_has_no_invented_timings(
    monkeypatch,
):
    audio = tts.MockTTS().synthesize("Fixture", "demo", "").audio
    observed = {}

    class Reply:
        status = 200

        def __init__(self):
            self.payload = io.BytesIO(audio)

        def getheader(self, name, default=None):
            return {"Content-Type": "audio/wav", "Content-Encoding": "identity"}.get(
                name, default
            )

        def read1(self, size):
            return self.payload.read(size)

    class Connection:
        sock = None

        def request(self, method, path, body, headers):
            observed.update(
                method=method, path=path, body=json.loads(body), headers=headers
            )

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(tts.settings, "ai_allowed_hosts", ["tts.example.com"])
    monkeypatch.setattr(
        tts.sources,
        "resolve",
        lambda host: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(tts, "llm_connection", lambda *args: Connection())
    result = tts.GenVoice(
        {"base_url": "https://tts.example.com/v1", "host": "tts.example.com"}
    ).synthesize("Fixture", "voice-id", "fixture-key")
    assert observed["method"] == "POST"
    assert observed["path"] == "/v1/api/tts"
    assert observed["body"] == {
        "text": "Fixture",
        "voice_id": "voice-id",
        "output_format": "wav_24000",
    }
    assert observed["headers"]["Authorization"] == "Bearer fixture-key"
    assert result.audio == audio and result.alignment == "none" and result.marks == []


def test_genvoice_blocks_non_public_dns_without_sending_key(monkeypatch):
    monkeypatch.setattr(tts.settings, "ai_allowed_hosts", ["tts.example.com"])
    monkeypatch.setattr(
        tts.sources,
        "resolve",
        lambda host: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(ProviderFailure, match="provider_url_blocked"):
        tts.GenVoice(
            {"base_url": "https://tts.example.com/v1", "host": "tts.example.com"}
        ).synthesize("Fixture", "voice", "fixture-key")


def test_yandex_speechkit_native_form_and_pcm_are_wrapped_as_wav(monkeypatch):
    from urllib.parse import parse_qs

    observed = {}
    pcm = b"\0" * 6400

    class Reply:
        status = 200

        def __init__(self):
            self.payload = io.BytesIO(pcm)

        def getheader(self, name, default=None):
            return {"Content-Type": "audio/x-pcm", "Content-Encoding": "identity"}.get(
                name, default
            )

        def read1(self, size):
            return self.payload.read(size)

    class Connection:
        sock = None

        def request(self, method, path, body, headers):
            observed.update(path=path, body=parse_qs(body.decode()), headers=headers)

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(tts.settings, "ai_allowed_hosts", ["tts.example.com"])
    monkeypatch.setattr(
        tts.sources,
        "resolve",
        lambda host: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(tts, "llm_connection", lambda *args: Connection())
    profile = {
        "protocol": "yandex_speechkit",
        "base_url": "https://tts.example.com",
        "host": "tts.example.com",
    }
    result = tts.get_provider(profile).synthesize(
        "Тест", "voice", "fixture-key", language="ru"
    )
    assert observed["path"] == "/speech/v1/tts:synthesize"
    assert observed["body"]["lang"] == ["ru-RU"]
    assert observed["body"]["format"] == ["lpcm"]
    assert observed["headers"]["Authorization"] == "Api-Key fixture-key"
    assert wav_duration(result.audio) == 200
    assert result.marks == [] and result.alignment == "none"
