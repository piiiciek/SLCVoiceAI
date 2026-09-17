"""Configuration loading for SLCVoiceAI."""

from __future__ import annotations

import os
import re
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

    #: Paste the key straight in here if you prefer. config.toml is in
    #: .gitignore precisely so this stays out of any repository - but if you
    #: share the file with anyone, strip it first.
    api_key: str = ""
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
    api_key: str = ""
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
    def api_key(self) -> str:
        return _resolve_key(self.llm.api_key, self.llm.api_key_env, "Anthropic",
                            "https://console.anthropic.com/")

    @property
    def gemini_key(self) -> str:
        return _resolve_key(self.gemini.api_key, self.gemini.api_key_env, "Gemini",
                            "https://aistudio.google.com/apikey")


#: Shapes of the keys we hand out instructions for, used only to spot one
#: that has been pasted somewhere it does nothing.
_KEY_SHAPES = re.compile(r"(AIza[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,})")


def _resolve_key(inline: str, env_var: str, service: str, signup_url: str) -> str:
    """The key from config.toml, else the environment, else a useful error.

    The error matters: the first person to configure this pasted their key
    into the commented-out `setx` example, where TOML ignored it silently and
    the only symptom was a window that flashed and vanished.
    """
    inline = (inline or "").strip()
    if inline:
        return inline
    key = os.environ.get(env_var, "").strip()
    if key:
        return key

    hint = ""
    stray = _find_stray_key()
    if stray:
        hint = ("\n\nThere is something shaped like an API key on line {line} "
                "of config.toml, inside a comment - comments are ignored. Move "
                "it to the api_key setting in that file's [{section}] section, "
                "without the leading '#'.".format(line=stray[0], section=stray[1]))

    raise RuntimeError(
        "No {service} API key. Either set api_key in config.toml, or set the "
        "{var} environment variable (setx {var} your-key, then open a NEW "
        "terminal). Free key: {url}{hint}".format(
            service=service, var=env_var, url=signup_url, hint=hint))


def _find_stray_key(path: str | Path = "config.toml") -> tuple[int, str] | None:
    """Line number and section of a key sitting uselessly in a comment."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    section = "?"
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]")
        if stripped.startswith("#") and _KEY_SHAPES.search(stripped):
            return number, section
    return None


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
