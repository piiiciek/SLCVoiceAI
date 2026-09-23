"""Speech to text at Groq, for people whose graphics card is already busy.

The local path in stt.py has one structural problem, and no amount of tuning
fixes it: the bridge shares a card with the simulator and the simulator wins.
Whichever way round you start them, one of the two loses - the model that is
already resident has its VRAM taken away, or it never gets any and drops to
`base` on the CPU. On 2026-09-23 the second happened, with 451 MB free, and
the flight got the weakest model in the table.

Sending the clip to Groq sidesteps the whole argument. The card stays the
simulator's, and every command gets whisper-large-v3 regardless.

What it costs is honest to state: the audio leaves the machine, and a command
spoken without a network connection is lost rather than merely slow. So this
is opt-in, `[stt] backend = "local"` remains the default, and `fallback_model`
exists for anyone who wants a local safety net.

Measured against the free tier, which bills a minimum of ten seconds per
request: a Krakow-Rome flight was 75 requests and 147 seconds of speech, so
750 seconds billed against a daily allowance of 28,800 - about three percent,
or room for some thirty-eight flights a day.
"""

from __future__ import annotations

import io
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

from .config import GroqSttConfig, SttConfig

log = logging.getLogger(__name__)

TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TRANSLATE_URL = "https://api.groq.com/openai/v1/audio/translations"

#: Only the full model can translate; the turbo variant transcribes and
#: nothing else. Worth knowing before a flight rather than during one.
TRANSLATES = ("whisper-large-v3",)

#: What arrives here is already 16 kHz mono float32 - the capture side
#: resamples before a clip is handed on, which is why stt.py hard-codes
#: the same number for its own timing check.
RATE = 16000


def to_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """A float32 mono clip as a 16-bit PCM WAV, in memory.

    Groq downsamples to 16 kHz mono itself, so there is nothing to gain by
    doing it here - but there is something to lose by clipping, hence the
    clamp before the scale.
    """
    clamped = np.clip(audio, -1.0, 1.0)
    pcm = (clamped * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _multipart(fields: dict[str, str], wav: bytes) -> tuple[bytes, str]:
    """Build a multipart/form-data body by hand.

    Rather than take a dependency on `requests` for one POST: the rest of the
    program talks to Gemini through urllib too, and a speech bridge that
    cannot be installed behind a corporate proxy because of a packaging
    choice would be a silly thing to ship.
    """
    boundary = "----SLCVoiceAI" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            ("--{b}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n"
             "{v}\r\n").format(b=boundary, n=name, v=value).encode("utf-8"))
    parts.append(
        ("--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
         "filename=\"speech.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
         ).format(b=boundary).encode("utf-8"))
    parts.append(wav)
    parts.append("\r\n--{b}--\r\n".format(b=boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


class GroqTranscriber:
    """Same shape as stt.Transcriber, so nothing upstream has to know."""

    def __init__(self, cfg: SttConfig, groq: GroqSttConfig, api_key: str):
        self.cfg = cfg
        self.groq = groq
        self._key = api_key
        # What the panel's subtitle shows. "groq" rather than a device,
        # because there is no device: the point of this class is that the
        # card is not involved.
        self._device = "groq"
        self._model_name = groq.model
        self._local = None            # built only if a fallback is wanted

        if cfg.task == "translate" and groq.model not in TRANSLATES:
            log.warning(
                "[stt] task = \"translate\" but %s only transcribes. Using "
                "transcription; set groq_model = \"whisper-large-v3\" if you "
                "want Groq to translate, or task = \"transcribe\" to match "
                "against the Polish half of aliases.py.", groq.model)

        self._prompt = ""
        if cfg.use_vocabulary:
            from .vocabulary import build_prompt, load_terms
            self._prompt = build_prompt(load_terms())
            log.info("Whisper vocabulary hint: %d chars", len(self._prompt))
        log.info("Speech to text: %s at Groq (nothing loaded locally)",
                 groq.model)

    # -- the interface stt.Transcriber offers -------------------------------

    def transcribe(self, audio: np.ndarray,
                   extra_terms: tuple[str, ...] = ()) -> tuple[str, str]:
        started = time.time()
        try:
            text, language = self._post(audio, extra_terms)
        except Exception as exc:
            return self._after_failure(exc, audio, extra_terms)
        took = time.time() - started
        log.info("Transcribed in %.2fs [%s]: %r", took, language, text)
        if took > self.groq.slow_seconds:
            log.warning(
                "That round trip to Groq took %.0fs. The audio is small - a "
                "flight is about two minutes of speech in total - so this is "
                "the connection rather than the clip. If it keeps up, "
                "[stt] backend = \"local\" is unaffected by it.", took)
        return text, language

    # -- the request --------------------------------------------------------

    def _post(self, audio: np.ndarray,
              extra_terms: tuple[str, ...]) -> tuple[str, str]:
        prompt = self._prompt
        if prompt and extra_terms:
            from .vocabulary import build_prompt, load_terms
            prompt = build_prompt(load_terms(), extra_terms)

        translating = (self.cfg.task == "translate"
                       and self.groq.model in TRANSLATES)
        fields = {
            "model": self.groq.model,
            # verbose_json so the reply names the language it heard. The plain
            # json format returns the text alone, and the log line that says
            # "[pl]" is how a wrong autodetect gets noticed at all.
            "response_format": "verbose_json",
        }
        if prompt:
            fields["prompt"] = prompt
        if self.cfg.language and not translating:
            # The translation endpoint always outputs English and rejects a
            # language hint; transcription takes one.
            fields["language"] = self.cfg.language

        body, content_type = _multipart(
            fields, to_wav(audio, RATE))
        request = urllib.request.Request(
            TRANSLATE_URL if translating else TRANSCRIBE_URL,
            data=body, method="POST",
            headers={"Authorization": "Bearer " + self._key,
                     "Content-Type": content_type})
        try:
            with urllib.request.urlopen(
                    request, timeout=self.groq.timeout_seconds) as reply:
                payload = json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._explain(exc) from exc
        text = (payload.get("text") or "").strip()
        language = payload.get("language") or self.cfg.language or ""
        # verbose_json spells the language out ("polish"), where the local
        # path reports a code ("pl"). The log, the panel and replay_log.py all
        # read that field, so it has to mean the same thing in both.
        return text, _as_code(language)

    def _explain(self, exc: urllib.error.HTTPError) -> Exception:
        """Turn an HTTP status into something worth reading mid-flight."""
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code == 429:
            return RuntimeError(
                "Groq is rate limiting. The free tier allows 20 requests a "
                "minute and 28,800 seconds of audio a day, which a flight "
                "does not come close to - so this is usually another program "
                "on the same key. " + detail)
        if exc.code in (401, 403):
            return RuntimeError(
                "Groq refused the key (HTTP {c}). Check groq_api_key in "
                "config.toml, or the GROQ_API_KEY environment variable."
                .format(c=exc.code))
        return RuntimeError("Groq HTTP {c}: {d}".format(c=exc.code, d=detail))

    # -- when it does not work ----------------------------------------------

    def _after_failure(self, exc: Exception, audio: np.ndarray,
                       extra_terms: tuple[str, ...]) -> tuple[str, str]:
        """Use the local model if one was asked for; otherwise say what broke.

        Losing the command silently is the one outcome worth ruling out. A
        pilot who says something and sees nothing happen assumes they were
        misheard and says it again, which is how five minutes go by.
        """
        if not self.groq.fallback_model:
            log.error("Speech to text failed: %s", exc)
            raise exc
        if self._local is None:
            log.warning("Groq failed (%s) - loading %s locally for the rest "
                        "of this session.", exc, self.groq.fallback_model)
            from dataclasses import replace

            from .stt import Transcriber
            self._local = Transcriber(
                replace(self.cfg, model=self.groq.fallback_model))
            self._device = self._local._device
            self._model_name = self.groq.fallback_model
        return self._local.transcribe(audio, extra_terms)


#: verbose_json names languages in full. Only the ones the panel speaks need
#: mapping; anything else passes through, which is better than guessing.
_CODES = {
    "english": "en", "polish": "pl", "german": "de", "french": "fr",
    "spanish": "es", "italian": "it", "dutch": "nl", "portuguese": "pt",
    "czech": "cs", "russian": "ru", "ukrainian": "uk",
}


def _as_code(language: str) -> str:
    low = (language or "").strip().lower()
    return _CODES.get(low, low)
