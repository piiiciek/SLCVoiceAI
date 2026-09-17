"""Speech to text via faster-whisper.

Runs locally on the GPU. Nothing spoken into the microphone leaves the
machine; only the resulting transcript is sent to the intent model.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from .config import SttConfig

log = logging.getLogger(__name__)


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
            if cfg.device == "cuda":
                log.warning("CUDA unavailable (%s) - falling back to CPU/int8", exc)
                self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
            else:
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
