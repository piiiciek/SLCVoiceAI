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
    model: str = "large-v3"
    device: str = "cuda"            # "cuda" or "cpu"
    compute_type: str = "float16"   # "int8" is a good CPU fallback
    language: str = ""              # "" = autodetect, "pl" / "en" to force
    beam_size: int = 5


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
    min_confidence: float = 0.55
    dry_run: bool = False
    speak_feedback: bool = False
    log_file: str = "slcvoiceai.log"


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    slc: SlcConfig = field(default_factory=SlcConfig)
    behaviour: BehaviourConfig = field(default_factory=BehaviourConfig)

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
    "llm": LlmConfig,
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
