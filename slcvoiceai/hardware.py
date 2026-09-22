"""Pick a Whisper model that fits the graphics card you actually have left.

The bridge shares a GPU with the simulator, and the simulator wins. On a
16 GB card MSFS 2024 routinely holds 15 GB, which left large-v3 in float16
fighting for the last few hundred megabytes and turning 2-second
transcriptions into 92-second ones.

Free memory, not total, is what matters - and it is measured at startup, so
start the bridge after the simulator if you want the choice to reflect
reality. Getting it wrong is no longer catastrophic (a stalled command is
dropped rather than pressed late), but it is still slow.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Measured on a live system with MSFS 2024 running, transcribing a 2.9s
#: Polish phrase. VRAM is what the model added on top of the simulator.
#:
#:   model      VRAM     per command   translation quality
#:   large-v3   1897 MB       1.61s    "Connect me with ground service."
#:   medium     1070 MB       1.25s    "Join me with a ground service."
#:   small       ~500 MB      0.96s    "Connect me with the service to the earth"
#:   base        ~290 MB      0.40s    weaker still outside English
#:
#: Each entry is (model, VRAM it needs, free VRAM required to choose it).
#: The gap between the two is headroom: the simulator can still grow after we
#: have loaded, and a model that only just fits is the one that thrashes.
_LADDER = (
    ("large-v3", 1900, 3600),
    ("medium", 1100, 2200),
    ("small", 500, 1100),
    ("base", 300, 700),
)

#: Below this much free VRAM the GPU is not worth using at all - measured,
#: small on the CPU (5.38s) still beat a starved GPU.
_MIN_USABLE_VRAM = 700


@dataclass
class GpuInfo:
    name: str
    free_mb: int
    total_mb: int

    def __str__(self) -> str:
        return "{name} ({free} MB free of {total} MB)".format(
            name=self.name, free=self.free_mb, total=self.total_mb)


def detect_gpu() -> GpuInfo | None:
    """Free and total VRAM of the first NVIDIA GPU, or None.

    Uses nvidia-smi because it reports *free* memory, which is the number
    that matters when something else already owns the card. Returns None on
    any failure - a missing tool, an AMD card, a driver that will not answer.
    """
    binary = shutil.which("nvidia-smi")
    if not binary:
        log.debug("nvidia-smi not found")
        return None
    try:
        result = subprocess.run(
            [binary, "--query-gpu=name,memory.free,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("nvidia-smi failed: %s", exc)
        return None

    first = result.stdout.strip().splitlines()
    if not first:
        return None
    parts = [p.strip() for p in first[0].split(",")]
    if len(parts) < 3:
        return None
    try:
        return GpuInfo(name=parts[0], free_mb=int(parts[1]), total_mb=int(parts[2]))
    except ValueError:
        return None


def choose(preferred: str = "auto") -> tuple[str, str, str]:
    """Return (model, device, compute_type) for the hardware available now.

    `preferred` other than "auto" is returned untouched: an explicit choice in
    config.toml is a decision, not a suggestion.
    """
    if preferred and preferred != "auto":
        return preferred, "cuda", "int8"

    gpu = detect_gpu()
    if gpu is None:
        log.warning("No NVIDIA GPU detected - running Whisper on the CPU with "
                    "'base'. Expect a second or two per command.")
        return "base", "cpu", "int8"

    if gpu.free_mb < _MIN_USABLE_VRAM:
        log.warning("Only %d MB of VRAM free on %s - the simulator has the card. "
                    "Using the CPU instead of fighting it for scraps.",
                    gpu.free_mb, gpu.name)
        return "base", "cpu", "int8"

    for model, needs, required_free in _LADDER:
        if gpu.free_mb >= required_free:
            log.info("%s -> Whisper %s on cuda (needs ~%d MB, %d MB free)",
                     gpu, model, needs, gpu.free_mb)
            if model in ("small", "base"):
                log.info("Tip: %r translates non-English literally. Start the "
                         "bridge before the simulator, or free VRAM, to get a "
                         "larger model.", model)
            return model, "cuda", "int8"

    # Free memory above the usable floor but below the smallest rung.
    log.warning("%s - tight, using 'base' on the GPU", gpu)
    return "base", "cuda", "int8"


def _download_note(model: str) -> str:
    """Whether choosing this model also means waiting for it.

    Asked here because this command is where somebody finds out which model
    they are getting, and its size is what decides whether the first start
    takes seconds or a coffee break.
    """
    from .stt import MODEL_MB, _cache_dir

    try:
        cached = _cache_dir() / ("models--Systran--faster-whisper-" + model)
        if cached.is_dir():
            return ""
        size = MODEL_MB.get(model)
        if size is None:
            return " It is not downloaded yet; the first start fetches it."
        return (" It is not downloaded yet: the first start fetches about "
                "{mb} MB, once.".format(mb=size))
    except Exception:  # pragma: no cover - a note is not worth an error
        return ""


def describe() -> str:
    """One-paragraph summary for --check-hardware."""
    gpu = detect_gpu()
    model, device, compute = choose("auto")
    waiting = _download_note(model)
    if gpu is None:
        return ("No NVIDIA GPU detected (or nvidia-smi is unavailable).\n"
                "Auto would pick: {m} on {d} ({c}).{w}".format(
                    m=model, d=device, c=compute, w=waiting))
    return ("GPU: {gpu}\n"
            "Auto would pick: {m} on {d} ({c}).{w}\n\n"
            "Free VRAM is measured now, so start the simulator first if you "
            "want this to reflect a real flight.".format(
                gpu=gpu, m=model, d=device, c=compute, w=waiting))
