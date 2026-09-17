"""Speech to text via faster-whisper.

Runs locally on the GPU. Nothing spoken into the microphone leaves the
machine; only the resulting transcript is sent to the intent model.
"""

from __future__ import annotations

import logging
import os
import time

import numpy as np

from .config import SttConfig

log = logging.getLogger(__name__)

# Hugging Face links cached model files into the snapshot directory with
# symlinks, which plain Windows accounts may not create ("WinError 1314: a
# required privilege is not held by the client") unless Developer Mode is on.
# Copying instead costs disk but works everywhere. Must be set before
# huggingface_hub is first imported, hence module scope.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


#: Substrings that identify a genuine GPU/driver problem in ctranslate2's or
#: CUDA's error text, as opposed to a download, disk or permission failure.
_CUDA_MARKERS = (
    "cuda", "cudnn", "cublas", "gpu", "no kernel image",
    "device ordinal", "out of memory", "nvidia",
)


def _is_cuda_failure(exc: Exception) -> bool:
    text = "{t} {m}".format(t=type(exc).__name__, m=exc).lower()
    # A privilege error is never a GPU problem, even if "cuda" appears in the
    # path of the file it failed on.
    if "winerror 1314" in text or "privilege" in text or "uprawnie" in text:
        return False
    return any(marker in text for marker in _CUDA_MARKERS)


class Transcriber:
    def __init__(self, cfg: SttConfig):
        from faster_whisper import WhisperModel

        self.cfg = cfg
        log.info("Loading Whisper %s on %s (%s)...",
                 cfg.model, cfg.device, cfg.compute_type)
        started = time.time()
        try:
            self.model = WhisperModel(
                cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        except Exception as exc:
            # Only retry on CPU for failures that are actually about the GPU.
            # Blaming CUDA for every exception sends you hunting for a driver
            # problem when the real fault was a download or a file permission,
            # and silently drops you onto a much slower path.
            if cfg.device == "cuda" and _is_cuda_failure(exc):
                log.warning("CUDA unavailable (%s) - falling back to CPU/int8", exc)
                self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
            else:
                log.error("Could not load Whisper %s on %s: %s",
                          cfg.model, cfg.device, exc)
                raise
        log.info("Whisper ready in %.1fs", time.time() - started)

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        """Return (text, detected_language) for a float32 mono clip."""
        started = time.time()
        segments, info = self.model.transcribe(
            audio,
            language=self.cfg.language or None,
            task=self.cfg.task,
            beam_size=self.cfg.beam_size,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info("Transcribed in %.2fs [%s]: %r",
                 time.time() - started, info.language, text)
        return text, info.language
