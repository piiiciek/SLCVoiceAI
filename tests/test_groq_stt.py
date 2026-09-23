"""Decoding speech somewhere other than the graphics card the simulator wants.

The local path has a problem no tuning fixes: whichever way round you start
the bridge and the simulator, one of them loses the VRAM. Groq sidesteps it
by not using the card at all.

Everything here runs without a network. What is worth pinning is the
plumbing - a clip becomes a WAV, the WAV becomes a multipart body, the reply
becomes (text, language) - and, just as much, what happens when the request
does not come back at all. A command that vanishes silently is the one
outcome worth ruling out: the pilot assumes they were misheard and says it
again, and five minutes go by.
"""

from __future__ import annotations

import io
import json
import sys
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai import groq_stt  # noqa: E402
from slcvoiceai.config import Config, GroqSttConfig, SttConfig  # noqa: E402


def a_clip(seconds: float = 2.0, level: float = 0.2) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(groq_stt.RATE * seconds))
            .astype(np.float32) * level)


@pytest.fixture
def cfg() -> SttConfig:
    return SttConfig(backend="groq", task="transcribe", use_vocabulary=False)


@pytest.fixture
def groq() -> GroqSttConfig:
    return GroqSttConfig(api_key="gsk_test")


class FakeReply:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def answering(monkeypatch, payload: dict, sent: list | None = None):
    def urlopen(request, timeout=None):
        if sent is not None:
            sent.append(request)
        return FakeReply(payload)
    monkeypatch.setattr(groq_stt.urllib.request, "urlopen", urlopen)


# -- the clip on the wire ---------------------------------------------------

def test_a_clip_becomes_a_wav_something_else_can_read():
    wav = groq_stt.to_wav(a_clip(1.0), groq_stt.RATE)
    with wave.open(io.BytesIO(wav), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == groq_stt.RATE
        assert handle.getnframes() == groq_stt.RATE


def test_a_loud_clip_is_clamped_rather_than_wrapped():
    """float32 past 1.0 scaled straight to int16 wraps round, and a wrapped
    sample is not loud - it is the opposite sign. That turns a shout into
    noise, which is the worst possible time to lose a command."""
    loud = np.array([2.0, -2.0, 0.0], dtype=np.float32)
    with wave.open(io.BytesIO(groq_stt.to_wav(loud, groq_stt.RATE)), "rb") as h:
        samples = np.frombuffer(h.readframes(3), dtype="<i2")
    assert samples[0] > 32000 and samples[1] < -32000


def test_the_body_carries_the_file_and_the_fields():
    body, content_type = groq_stt._multipart(
        {"model": "whisper-large-v3-turbo"}, b"RIFFxxxx")
    boundary = content_type.split("boundary=")[1]
    assert body.count(boundary.encode()) == 3        # two parts and the close
    assert b'name="model"' in body
    assert b'filename="speech.wav"' in body
    assert b"RIFFxxxx" in body
    assert body.endswith(b"--\r\n")


# -- the reply --------------------------------------------------------------

def test_the_reply_becomes_text_and_a_language_code(monkeypatch, cfg, groq):
    answering(monkeypatch, {"text": " mozecie tankowac ", "language": "polish"})
    text, language = groq_stt.GroqTranscriber(cfg, groq, "k").transcribe(a_clip())
    assert text == "mozecie tankowac"
    # "polish" is what verbose_json says; "pl" is what the log, the panel and
    # replay_log.py all read. They have to be the same thing.
    assert language == "pl"


def test_an_unknown_language_passes_through(monkeypatch, cfg, groq):
    answering(monkeypatch, {"text": "hi", "language": "faroese"})
    _, language = groq_stt.GroqTranscriber(cfg, groq, "k").transcribe(a_clip())
    assert language == "faroese", "better an odd label than a wrong one"


def test_transcription_is_asked_for_by_default(monkeypatch, cfg, groq):
    sent: list = []
    answering(monkeypatch, {"text": "", "language": "english"}, sent)
    groq_stt.GroqTranscriber(cfg, groq, "k").transcribe(a_clip())
    assert sent[0].full_url == groq_stt.TRANSCRIBE_URL
    assert sent[0].headers["Authorization"] == "Bearer k"


def test_turbo_never_reaches_the_translation_endpoint(monkeypatch, groq, caplog):
    """whisper-large-v3-turbo transcribes and nothing else, so asking it to
    translate would fail every command. Say so at startup instead."""
    sent: list = []
    answering(monkeypatch, {"text": "", "language": "polish"}, sent)
    translating = SttConfig(backend="groq", task="translate",
                            use_vocabulary=False)
    with caplog.at_level("WARNING"):
        who = groq_stt.GroqTranscriber(translating, groq, "k")
    assert "only transcribes" in caplog.text
    who.transcribe(a_clip())
    assert sent[0].full_url == groq_stt.TRANSCRIBE_URL


def test_the_full_model_does_translate(monkeypatch, groq):
    sent: list = []
    answering(monkeypatch, {"text": "you can refuel", "language": "english"}, sent)
    translating = SttConfig(backend="groq", task="translate",
                            use_vocabulary=False, language="pl")
    who = groq_stt.GroqTranscriber(
        translating, replace(groq, model="whisper-large-v3"), "k")
    who.transcribe(a_clip())
    assert sent[0].full_url == groq_stt.TRANSLATE_URL
    # The translation endpoint always emits English and refuses a language
    # hint, so the one from config must not be forwarded to it.
    assert b'name="language"' not in sent[0].data


def test_a_forced_language_is_forwarded_when_transcribing(monkeypatch, groq):
    sent: list = []
    answering(monkeypatch, {"text": "", "language": "polish"}, sent)
    forced = SttConfig(backend="groq", task="transcribe",
                       use_vocabulary=False, language="pl")
    groq_stt.GroqTranscriber(forced, groq, "k").transcribe(a_clip())
    assert b'name="language"' in sent[0].data


# -- when it does not come back ---------------------------------------------

def refusing(monkeypatch, exc: Exception):
    def urlopen(request, timeout=None):
        raise exc
    monkeypatch.setattr(groq_stt.urllib.request, "urlopen", urlopen)


def an_http_error(code: int) -> groq_stt.urllib.error.HTTPError:
    return groq_stt.urllib.error.HTTPError(
        groq_stt.TRANSCRIBE_URL, code, "no", {}, io.BytesIO(b'{"error":"x"}'))


@pytest.mark.parametrize("code,expected", [
    (401, "refused the key"),
    (403, "refused the key"),
    (429, "rate limiting"),
    (500, "HTTP 500"),
])
def test_a_refusal_says_what_to_do_about_it(monkeypatch, cfg, groq,
                                            code, expected):
    refusing(monkeypatch, an_http_error(code))
    with pytest.raises(Exception) as caught:
        groq_stt.GroqTranscriber(cfg, groq, "k").transcribe(a_clip())
    assert expected in str(caught.value)


def test_without_a_fallback_the_failure_is_raised_not_swallowed(
        monkeypatch, cfg, groq):
    """Returning empty text here would read as "you said nothing", and the
    pilot would repeat themselves into a bridge that cannot hear."""
    refusing(monkeypatch, OSError("no route to host"))
    with pytest.raises(OSError):
        groq_stt.GroqTranscriber(cfg, groq, "k").transcribe(a_clip())


def test_a_named_fallback_takes_over_for_the_rest_of_the_session(
        monkeypatch, cfg, groq):
    refusing(monkeypatch, OSError("no route to host"))
    built: list = []

    class FakeLocal:
        def __init__(self, stt_cfg):
            built.append(stt_cfg.model)
            self._device = "cuda"

        def transcribe(self, audio, extra_terms=()):
            return "from the local model", "pl"

    import slcvoiceai.stt as stt
    monkeypatch.setattr(stt, "Transcriber", FakeLocal)

    who = groq_stt.GroqTranscriber(cfg, replace(groq, fallback_model="small"), "k")
    assert who.transcribe(a_clip())[0] == "from the local model"
    assert who.transcribe(a_clip())[0] == "from the local model"
    assert built == ["small"], "the local model is loaded once, not per command"
    assert who._device == "cuda", "the panel has to stop saying 'groq'"


# -- choosing between the two ----------------------------------------------

def test_the_factory_reads_the_backend(monkeypatch):
    import slcvoiceai.stt as stt

    monkeypatch.setattr(stt, "Transcriber", lambda cfg: "local one")
    assert stt.build(Config()) == "local one"

    monkeypatch.setattr(groq_stt, "GroqTranscriber",
                        lambda cfg, groq, key: ("groq one", key))
    cfg = Config(stt=SttConfig(backend="groq"),
                 groq=GroqSttConfig(api_key="gsk_test"))
    assert stt.build(cfg) == ("groq one", "gsk_test")


def test_an_unknown_backend_is_refused_before_anything_loads():
    with pytest.raises(ValueError, match="local"):
        import slcvoiceai.stt as stt
        stt.build(Config(stt=SttConfig(backend="cloud")))
