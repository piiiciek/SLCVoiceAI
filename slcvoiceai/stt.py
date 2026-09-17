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


def _register_nvidia_dlls() -> list[str]:
    """Let Windows find the CUDA runtime DLLs shipped in the venv.

    ctranslate2 needs cuBLAS and cuDNN at *inference* time, not at load time -
    the model loads happily on the GPU and then the first transcription dies
    with "Library cublas64_12.dll is not found". The pip packages
    nvidia-cublas-cu12 / nvidia-cudnn-cu12 install those DLLs under
    site-packages\\nvidia\\*\\bin, which is not on the DLL search path, so
    they have to be registered explicitly before ctranslate2 is imported.
    """
    if os.name != "nt":
        return []
    import site

    added: list[str] = []
    roots = list(site.getsitepackages())
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        roots.append(user_site)

    for root in roots:
        nvidia = os.path.join(root, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for package in sorted(os.listdir(nvidia)):
            bin_dir = os.path.join(nvidia, package, "bin")
            if not os.path.isdir(bin_dir):
                continue
            try:
                os.add_dll_directory(bin_dir)
            except OSError:
                pass
            added.append(bin_dir)

    # add_dll_directory only covers loads that go through Python's loader with
    # LOAD_LIBRARY_SEARCH_USER_DIRS. ctranslate2 resolves cuBLAS with a plain
    # LoadLibrary, which ignores it and searches PATH instead - so prepend
    # there too, or the model loads on the GPU and the first transcription
    # still dies with "cublas64_12.dll is not found".
    if added:
        current = os.environ.get("PATH", "")
        missing = [d for d in added if d not in current]
        if missing:
            os.environ["PATH"] = os.pathsep.join(missing + [current])
    return added


_NVIDIA_DLL_DIRS = _register_nvidia_dlls()


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


def _resolve(cfg: SttConfig) -> tuple[str, str, str]:
    """Settle on (model, device, compute_type), asking the hardware if asked to.

    Any explicit value in config.toml wins; "auto" defers to what the card can
    actually spare right now.
    """
    from .hardware import choose

    if cfg.model != "auto":
        return cfg.model, cfg.device, cfg.compute_type
    model, device, compute_type = choose("auto")
    # A deliberate device or quantisation still overrides the auto choice.
    if cfg.device != "auto":
        device = cfg.device
    if cfg.compute_type != "auto":
        compute_type = cfg.compute_type
    return model, device, compute_type


class Transcriber:
    def __init__(self, cfg: SttConfig):
        from faster_whisper import WhisperModel

        self.cfg = cfg
        model, device, compute_type = _resolve(cfg)
        log.info("Loading Whisper %s on %s (%s)...", model, device, compute_type)
        started = time.time()
        try:
            self.model = WhisperModel(
                model, device=device, compute_type=compute_type)
            self._device = device
            self._model_name = model
        except Exception as exc:
            # Only retry on CPU for failures that are actually about the GPU.
            # Blaming CUDA for every exception sends you hunting for a driver
            # problem when the real fault was a download or a file permission,
            # and silently drops you onto a much slower path.
            if device == "cuda" and _is_cuda_failure(exc):
                log.warning("CUDA unavailable (%s) - falling back to CPU/int8", exc)
                self.model = WhisperModel(model, device="cpu", compute_type="int8")
                self._device = "cpu"
            else:
                log.error("Could not load Whisper %s on %s: %s", model, device, exc)
                raise
        if _NVIDIA_DLL_DIRS:
            log.debug("Registered CUDA DLL directories: %s", _NVIDIA_DLL_DIRS)
        self._warm_up()
        log.info("Whisper ready in %.1fs (%s)", time.time() - started, self._device)

    def _warm_up(self) -> None:
        """Burn the first-call cost now instead of on the pilot's first command.

        CUDA kernel setup makes the very first transcription ~10s; every one
        after it is ~0.1s. Paying that during startup keeps the wait where the
        user expects one, and also means a broken CUDA runtime is caught and
        falls back before anyone is mid-flight.
        """
        try:
            # Low-level noise, not silence: the VAD filter strips pure zeros
            # before they ever reach the encoder, so a silent warm-up warms
            # nothing and the first real command still pays the full ~7s.
            rng = np.random.default_rng(0)
            noise = (rng.standard_normal(self.cfg.sample_warmup_frames)
                     .astype(np.float32) * 0.05)
            self.transcribe(noise)
        except Exception as exc:
            log.warning("Warm-up transcription failed (%s) - continuing anyway", exc)

    def _fall_back_to_cpu(self, exc: Exception) -> bool:
        """Swap the GPU model for a CPU one after a CUDA failure at runtime.

        Returns False if we are already on the CPU, so the caller re-raises
        instead of looping.
        """
        from faster_whisper import WhisperModel

        if self._device == "cpu":
            return False
        log.warning("CUDA failed during transcription (%s) - switching to CPU "
                    "for the rest of this session", exc)
        self.model = WhisperModel(self._model_name, device="cpu", compute_type="int8")
        self._device = "cpu"
        return True

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        """Return (text, detected_language) for a float32 mono clip."""
        try:
            return self._transcribe(audio)
        except Exception as exc:
            # cuBLAS/cuDNN problems surface here, not at load time. Degrade to
            # the CPU rather than failing every single utterance.
            if _is_cuda_failure(exc) and self._fall_back_to_cpu(exc):
                return self._transcribe(audio)
            raise

    def _transcribe(self, audio: np.ndarray) -> tuple[str, str]:
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
