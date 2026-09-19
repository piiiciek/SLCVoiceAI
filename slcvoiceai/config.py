"""Configuration loading for SLCVoiceAI."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import threading
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
class UiConfig:
    #: Language for the control panel. "auto" follows the Windows display
    #: language and falls back to English. Only the interface is translated;
    #: the log stays in English so it can be read by anyone helping, and so
    #: the tools that parse it keep working.
    language: str = "auto"


@dataclass
class BehaviourConfig:
    min_confidence: float = 0.65
    dry_run: bool = False
    speak_feedback: bool = False
    #: Refuse to act on a command that took longer than this to transcribe.
    #: 0 disables the check.
    max_command_age_seconds: float = 12.0
    #: Read SLC's buttons the moment the push-to-talk key goes down, so the
    #: scan runs while the pilot is still speaking rather than afterwards.
    prescan: bool = True
    #: How old that scan may be by the time it is used. It starts with the
    #: key, so its age is however long the key was held plus transcription -
    #: comfortably inside this unless someone holds the key through a very
    #: long sentence, in which case SLC is simply read again. 0 disables it.
    max_scan_age_seconds: float = 30.0
    #: Ask GitHub once at startup whether a newer version has been pushed,
    #: and say so. Nothing downloads or replaces itself either way.
    check_for_updates: bool = True

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
    ui: UiConfig = field(default_factory=UiConfig)

    #: Where this was loaded from, so the panel can write a setting back to
    #: the same file. None when nobody loaded it from disk, which is what
    #: stops tests and headless defaults writing anything.
    source: Path | None = field(default=None, compare=False)

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
    "ui": UiConfig,
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
    return Config(source=path, **kwargs)


# --------------------------------------------------------------------------
# Writing one setting back
# --------------------------------------------------------------------------
#
# The panel changes three things - the confidence floor, dry run and the
# interface language - and until now they lasted until the window closed,
# which is a confusing thing for a control to do.
#
# Nothing here serialises a Config. It patches the individual lines it was
# asked about and leaves every other byte of the file alone. That is not
# tidiness, it is the safety property: config.toml holds the API key, and
# a key can also come from an environment variable, where `cfg.api_key`
# resolves to a value that must never be written to disk. A whole-file
# dump would do exactly that, and would throw away the comments that
# explain every setting. A line patcher cannot.


def _as_toml(value) -> str:
    """One value, as TOML spells it."""
    if isinstance(value, bool):        # before int: bool is an int
        return "true" if value else "false"
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, float):
        text = format(value, "g")
        # TOML reads 1 as an integer, which would quietly change the type
        # of a float setting the next time it is loaded.
        return text if ("." in text or "e" in text) else text + ".0"
    return '"{v}"'.format(v=str(value).replace("\\", "\\\\").replace('"', '\\"'))


def _patch(text: str, section: str, key: str, value: str) -> str:
    """Set one key inside one section, touching nothing else."""
    lines = text.splitlines()
    header = re.compile(r"^\s*\[" + re.escape(section) + r"\]\s*$")
    assignment = re.compile(
        r"^(\s*" + re.escape(key) + r"\s*=\s*)(.*?)(\s*#.*)?$")

    at = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if at is None:
        blank = [""] if lines else []
        lines += blank + ["[{s}]".format(s=section),
                          "{k} = {v}".format(k=key, v=value)]
        return "\n".join(lines) + "\n"

    for i in range(at + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            break                        # the next section began
        found = assignment.match(lines[i])
        if found:
            lines[i] = found.group(1) + value + (found.group(3) or "")
            return "\n".join(lines) + "\n"

    # The section is there but says nothing about this key.
    lines.insert(at + 1, "{k} = {v}".format(k=key, v=value))
    return "\n".join(lines) + "\n"


#: Read-modify-write is not atomic, and the panel's controls arrive on
#: whatever thread pywebview dispatches them on. Without this, two settings
#: changed in quick succession both read the old file and the second write
#: silently threw the first away - which is exactly what happened the first
#: time this was tried end to end.
_SAVE_LOCK = threading.Lock()


def save_settings(path: str | Path, changes: dict[str, dict]) -> None:
    """Write settings back, as {section: {key: value}}.

    Replaced through a temporary file in the same directory, so a crash
    mid-write cannot leave a half-written config.toml behind - it holds
    the API key, and losing it to a truncated file would be a bad day.
    """
    path = Path(path)
    with _SAVE_LOCK:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for section, values in changes.items():
            for key, value in values.items():
                text = _patch(text, section, key, _as_toml(value))

        # A unique name, not <file>.tmp. The lock keeps this process
        # honest, but two panels open at once would otherwise write the
        # same temporary file and hand each other half of it - which does
        # not lose a setting, it corrupts config.toml. Worth the extra
        # line, given what that file holds.
        handle, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        os.close(handle)
        try:
            Path(temporary).write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        except BaseException:
            # Never leave a stray half-written config beside the real one.
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
