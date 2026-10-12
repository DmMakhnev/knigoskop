"""Validate real marks; never estimate word positions for unaligned speech."""

import io
import re
import wave

from app.integrations.llm import ProviderFailure


def segments(text, maximum):
    result = []
    start = 0
    while start < len(text):
        end = min(start + maximum, len(text))
        if end < len(text):
            space = text.rfind(" ", start + maximum // 2, end)
            if space > start:
                end = space + 1
        result.append((start, end))
        start = end
    return result


def wav_duration(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            duration = audio.getnframes() * 1000 // audio.getframerate()
            if audio.getcomptype() != "NONE" or not 0 < duration <= 300000:
                raise ValueError()
            if (
                len(audio.readframes(audio.getnframes()))
                != audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            ):
                raise ValueError()
            return duration
    except (ValueError, EOFError, wave.Error, ZeroDivisionError):
        raise ProviderFailure("audio_content_invalid") from None


def validated_marks(text, marks, duration):
    words = list(re.finditer(r"\S+", text))
    if len(words) != len(marks):
        raise ProviderFailure("audio_timings_invalid")
    previous = 0
    for word, mark in zip(words, marks, strict=True):
        if (
            set(mark) != {"start_ms", "end_ms", "text_start", "text_end"}
            or any(type(value) is not int for value in mark.values())
            or mark["text_start"] != word.start()
            or mark["text_end"] != word.end()
            or not previous <= mark["start_ms"] < mark["end_ms"] <= duration
        ):
            raise ProviderFailure("audio_timings_invalid")
        previous = mark["end_ms"]
    return marks
