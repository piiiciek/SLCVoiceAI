"""Configuration loading for SLCVoiceAI."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AudioConfig:
    ptt_key: str = "f13"
    input_device: str = ""          # "" = Windows default recording device
    sample_rate: int = 16000
    max_seconds: float = 20.0
    min_seconds: float = 0.35       # ignore accidental key taps


@dataclass
class SttConfig:
    model: str = "auto"
    device: str = "auto"            # "auto", "cuda" or "cpu"
    compute_type: str = "auto"      # "auto", "int8" or "float16"
    language: str = ""              # "" = autodetect, "pl" / "en" to force
    #: 1 (greedy). Measured on real speech with the simulator running,
    #: beam_size=5 took 6.00s against 1.17s for the same model and produced
    #: byte-identical text: command phrases are short and unambiguous, so the
    #: extra search buys nothing and costs a five-fold wait.
    beam_size: int = 1

    # "translate" makes Whisper emit English whatever you speak, which is what
    # lets the offline fuzzy backend match Polish speech against SLC's English
    # button names. "transcribe" keeps your own language.
    task: str = "translate"

    #: Seed Whisper with the vocabulary of a cabin before it decodes. Costs
    #: nothing and heads off literal renderings at the source - see
    #: vocabulary.py. Edit vocabulary.txt next to config.toml to change it.
    use_vocabulary: bool = True

    #: Samples of silence pushed through the model at startup to pay the CUDA
    #: first-call cost (~10s) up front rather than on the first real command.
    sample_warmup_frames: int = 16000


@dataclass
class GeminiConfig:
    #: BlueLine Realism uses this model on Google's free tier and it is
    #: plenty for picking one button off a short list.
    model: str = "gemini-3.1-flash-lite"
    fallback_model: str = "gemini-3.1-flash-lite"
    api_key_env: str = "GEMINI_API_KEY"
    max_tokens: int = 512
    timeout_seconds: float = 20.0


@dataclass
class IntentConfig:
    #: "fuzzy"  - offline, free, no API key, no extra VRAM.
    #: "claude" - Anthropic API; better at loose and idiomatic phrasing, costs
    #:            roughly a third of a grosz per command.
    backend: str = "fuzzy"

    #: Who to ask when the offline matcher cannot settle an utterance:
    #: "none", "gemini" or "claude". Nothing is sent anywhere while the
    #: local layer is confident, which is most of the time.
    escalate_to: str = "none"


@dataclass
class LlmConfig:
    model: str = "claude-haiku-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = 512
    timeout_seconds: float = 20.0


@dataclass
class SlcConfig:
    process_name: str = "SLC.exe"
    stream_export_dir: str = ""     # SLC Settings -> ExportStreamDataFolder
    refresh_timeout: float = 2.0


@dataclass
class BehaviourConfig:
    min_confidence: float = 0.65
    dry_run: bool = False
    speak_feedback: bool = False
    #: Refuse to act on a command that took longer than this to transcribe.
    #: 0 disables the check.
    max_command_age_seconds: float = 12.0

    log_file: str = "slcvoiceai.log"


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    intent: IntentConfig = field(default_factory=IntentConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    slc: SlcConfig = field(default_factory=SlcConfig)
    behaviour: BehaviourConfig = field(default_factory=BehaviourConfig)

    @property
    def needs_api_key(self) -> bool:
        return "claude" in (self.intent.backend, self.intent.escalate_to)

    @property
    def needs_gemini_key(self) -> bool:
        return "gemini" in (self.intent.backend, self.intent.escalate_to)

    @property
    def gemini_key(self) -> str:
        key = os.environ.get(self.gemini.api_key_env, "")
        if not key:
            raise RuntimeError(
                "Environment variable {var} is not set. Get a free key at "
                "https://aistudio.google.com/apikey then run:  setx {var} "
                "your-key".format(var=self.gemini.api_key_env))
        return key

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.llm.api_key_env, "")
        if not key:
            raise RuntimeError(
                "Environment variable {var} is not set. Put your Anthropic API "
                "key there, e.g.  setx {var} sk-ant-...".format(var=self.llm.api_key_env)
            )
        return key


_SECTIONS = {
    "audio": AudioConfig,
    "stt": SttConfig,
    "intent": IntentConfig,
    "llm": LlmConfig,
    "gemini": GeminiConfig,
    "slc": SlcConfig,
    "behaviour": BehaviourConfig,
}


def load(path: str | Path = "config.toml") -> Config:
    """Load config.toml, falling back to defaults for anything absent."""
    path = Path(path)
    raw: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

    kwargs = {}
    for section, cls in _SECTIONS.items():
        values = raw.get(section, {})
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(values) - known
        if unknown:
            raise ValueError(
                "Unknown key(s) in [{section}] of {path}: {keys}".format(
                    section=section, path=path, keys=", ".join(sorted(unknown)))
            )
        kwargs[section] = cls(**values)
    return Config(**kwargs)
